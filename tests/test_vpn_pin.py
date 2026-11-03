import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import vpn_pin as vpn


class Profiles(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profiles.conf"
            path.write_text(text)
            return vpn.load_profiles(path)

    def test_spaces_and_optional_kubernetes(self):
        profiles = self.load("# private examples\nwork|/private/vpn files/client.ovpn|api.example.test||\n")
        self.assertEqual(profiles["work"].ovpn, Path("/private/vpn files/client.ovpn"))
        self.assertEqual(profiles["work"].targets, ["api.example.test"])

    def test_non_executable_config_and_invalid_names(self):
        for text in ["../work|/client.ovpn|192.0.2.1||", "all|/client.ovpn|192.0.2.1||",
                     "work|relative.ovpn|192.0.2.1||", "work|/client.ovpn|||",
                     "work|/client.ovpn|$(touch /tmp/should-not-exist)||",
                     "work|/client.ovpn|host,,other||", "work|/client.ovpn|host|||extra"]:
            with self.subTest(text=text), self.assertRaises(vpn.Error):
                self.load(text)
        with self.assertRaises(vpn.Error):
            self.load("work|/client.ovpn|192.0.2.1||\nwork|/client.ovpn|192.0.2.2||")

    def test_host_and_kube_addresses_are_combined_and_deduplicated(self):
        profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1", "api.example.test"], "test", "")
        entries = ["192.0.2.1", "192.0.2.2"]
        with patch.object(vpn, "as_user", return_value="https://kube.example.test:6443"), \
                patch.object(vpn, "executable", return_value="/stub/kubectl"), \
                patch.object(vpn, "lookup_ipv4", return_value=entries):
            self.assertEqual(vpn.resolve_ips(profile), ["192.0.2.1", "192.0.2.2"])

    def test_invalid_numeric_ip_and_dns_failure(self):
        profile = vpn.Profile("work", Path("/client.ovpn"), ["999.0.2.1"], "", "")
        with self.assertRaises(vpn.Error):
            vpn.resolve_ips(profile)
        profile.targets = ["api.example.test"]
        with patch.object(vpn, "lookup_ipv4", side_effect=vpn.ResolutionError("offline")), self.assertRaises(vpn.Error):
            vpn.resolve_ips(profile)

    def test_missing_executable_and_intel_homebrew(self):
        with patch.object(vpn.shutil, "which", return_value=None), \
                patch.object(vpn.os.path, "isfile", return_value=False):
            with self.assertRaisesRegex(vpn.Error, "missing executable"):
                vpn.executable("kubectl")
        with patch.object(vpn.shutil, "which", return_value=None), \
                patch.object(vpn.os.path, "isfile", side_effect=lambda p: p == "/usr/local/bin/kubectl"), \
                patch.object(vpn.os, "access", return_value=True), \
                patch.object(Path, "resolve", return_value=Path("/usr/local/bin/kubectl")):
            self.assertEqual(vpn.executable("kubectl"), "/usr/local/bin/kubectl")


class Routes(unittest.TestCase):
    def setUp(self):
        self.profile = vpn.Profile("work", Path("/client.ovpn"), [], "", "")
        self.state = {"pid": 123, "started": "start", "utun": "utun42", "ifindex": 42, "routes": ["192.0.2.3"]}
        log = b"fixture initial log\n"
        guard = {"device": 1, "inode": 2, "size": len(log), "sha256": hashlib.sha256(log).hexdigest()}
        self.state["log_guard"] = guard
        self.routes = {"192.0.2.3": "utun42"}
        self.saved = []
        self.calls = []
        self.fail_add = set()
        self.fail_remove = set()
        patches = [patch.object(vpn.socket, "if_nametoindex", return_value=42),
                   patch.object(vpn, "log_snapshot", return_value=(log, guard)),
                   patch.object(vpn, "resolve_ips", return_value=["192.0.2.1", "192.0.2.2"]),
                   patch.object(vpn, "exact_route", side_effect=lambda ip: self.routes.get(ip)),
                   patch.object(vpn, "add_route", side_effect=self.add),
                   patch.object(vpn, "remove_route", side_effect=self.remove),
                   patch.object(vpn, "save_state", side_effect=lambda d, n, s: self.saved.append(json.loads(json.dumps(s))))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def add(self, ip, interface):
        self.calls.append(("add", ip))
        if ip in self.fail_add:
            raise vpn.Error("add failed")
        self.routes[ip] = interface

    def remove(self, ip, interface):
        self.calls.append(("delete", ip))
        if ip in self.fail_remove:
            raise vpn.Error("delete failed")
        self.routes.pop(ip, None)

    def pin(self):
        with contextlib.redirect_stdout(io.StringIO()):
            vpn.pin_routes(Path("/unused"), self.profile, self.state)

    def test_resolution_failure_preserves_routes_and_state(self):
        with patch.object(vpn, "resolve_ips", side_effect=vpn.Error("DNS failed")), self.assertRaises(vpn.Error):
            self.pin()
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})
        self.assertFalse(self.calls)
        self.assertFalse(self.saved)

    def test_conflict_is_not_overwritten_including_another_profile_on_same_interface(self):
        for interface in ["utun9", "utun42"]:
            with self.subTest(interface=interface):
                self.routes["192.0.2.1"] = interface
                with self.assertRaisesRegex(vpn.Error, "conflict"):
                    self.pin()
                self.assertFalse(self.calls)

    def test_new_routes_added_before_old_removed(self):
        self.pin()
        self.assertEqual(self.calls, [("add", "192.0.2.1"), ("add", "192.0.2.2"), ("delete", "192.0.2.3")])
        self.assertEqual(self.saved[-1]["routes"], ["192.0.2.1", "192.0.2.2"])

    def test_add_failure_rolls_back_and_reports_failure(self):
        self.fail_add.add("192.0.2.2")
        with self.assertRaisesRegex(vpn.Error, "previous routes restored"):
            self.pin()
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})
        self.assertEqual(self.saved[-1]["routes"], ["192.0.2.3"])

    def test_remove_failure_rolls_back_new_routes(self):
        self.fail_remove.add("192.0.2.3")
        with self.assertRaises(vpn.Error):
            self.pin()
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})

    def test_failed_rollback_records_actual_routes(self):
        self.fail_add.add("192.0.2.2")
        self.fail_remove.add("192.0.2.1")
        with self.assertRaisesRegex(vpn.Error, "rollback incomplete"):
            self.pin()
        self.assertEqual(self.saved[-1]["routes"], ["192.0.2.1", "192.0.2.3"])

    def test_interrupt_after_route_command_keeps_cleanup_information(self):
        def interrupted(ip, interface):
            self.assertEqual(self.saved[0]["pending_routes"], ["192.0.2.1", "192.0.2.2", "192.0.2.3"])
            self.add(ip, interface)
            raise KeyboardInterrupt()
        with patch.object(vpn, "add_route", side_effect=interrupted), self.assertRaisesRegex(vpn.Error, "unfinished journal"):
            self.pin()
        self.assertIn("192.0.2.1", self.saved[-1]["pending_routes"])
        self.assertNotIn("192.0.2.1", self.saved[-1]["routes"])

    def test_final_state_write_failure_restores_previous_routes(self):
        attempts = []
        def save(directory, name, state):
            attempts.append(json.loads(json.dumps(state)))
            if len(attempts) == 2:
                raise OSError("disk full")
        with patch.object(vpn, "save_state", side_effect=save), self.assertRaisesRegex(vpn.Error, "previous routes restored"):
            self.pin()
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})
        self.assertIn("pending_routes", attempts[0])
        self.assertEqual(attempts[-1]["routes"], ["192.0.2.3"])

    def test_persistent_write_failure_leaves_journal_for_manual_recovery(self):
        attempts = []
        def save(directory, name, state):
            attempts.append(json.loads(json.dumps(state)))
            if len(attempts) == 1:
                self.saved.append(attempts[-1])
            else:
                raise OSError("disk full")
        with patch.object(vpn, "save_state", side_effect=save), self.assertRaisesRegex(vpn.Error, "inspect recorded intent"):
            self.pin()
        self.assertIn("pending_routes", self.saved[-1])
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})

    def test_route_appearing_after_plan_is_not_overwritten_or_claimed(self):
        reads = []
        def route(ip):
            if ip == "192.0.2.1":
                reads.append(ip)
                if len(reads) > 1:
                    self.routes[ip] = "utun42"
            return self.routes.get(ip)
        with patch.object(vpn, "exact_route", side_effect=route), self.assertRaisesRegex(vpn.Error, "changed after planning"):
            self.pin()
        self.assertFalse(self.calls)
        self.assertEqual(self.routes["192.0.2.1"], "utun42")
        self.assertNotIn("192.0.2.1", self.saved[-1]["routes"])

    def test_interface_generation_change_before_add_leaves_routes_untouched(self):
        with patch.object(vpn.socket, "if_nametoindex", side_effect=[42, 99]), self.assertRaisesRegex(vpn.Error, "interface identity changed"):
            self.pin()
        self.assertFalse(self.calls)
        self.assertEqual(self.routes, {"192.0.2.3": "utun42"})


class StateAndProcess(unittest.TestCase):
    def test_process_identity_requests_untruncated_command(self):
        with patch.object(vpn, "run", return_value="command") as run:
            self.assertEqual(vpn.process_value(123, "command"), "command")
        run.assert_called_once_with(["/bin/ps", "-ww", "-p", "123", "-o", "command="], check=False)

    def test_spawn_failure_uses_route_rollback_error_type(self):
        with patch.object(vpn.subprocess, "run", side_effect=OSError("cannot spawn")):
            with self.assertRaisesRegex(vpn.Error, "cannot run"):
                vpn.run(["/stub/route"])

    def test_sudo_escalation_preserves_interpreter_and_config_path(self):
        profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1"], "", "")
        with patch.object(vpn.sys, "platform", "darwin"), patch.object(vpn, "load_profiles", return_value={"work": profile}), \
                patch.object(vpn.os, "geteuid", return_value=501), \
                patch.object(vpn.os, "execv", side_effect=RuntimeError("exec")) as execute, self.assertRaises(RuntimeError):
            vpn.main(["--config", "/private/config files/profiles.conf", "refresh", "work"])
        command = execute.call_args.args[1]
        self.assertEqual(command[1], sys.executable)
        self.assertEqual(command[-4:], ["--config", "/private/config files/profiles.conf", "refresh", "work"])

    def test_kube_exec_preserves_terminal_and_exit_status(self):
        profile = vpn.Profile("work", Path("/client.ovpn"), [], "development", "")
        with patch.object(vpn.sys, "platform", "darwin"), patch.object(vpn, "load_profiles", return_value={"work": profile}), \
                patch.object(vpn, "executable", return_value="/stub/kubectl"), patch.object(vpn.os, "geteuid", return_value=501), \
                patch.object(vpn.os, "execv", side_effect=RuntimeError("exec")) as execute, self.assertRaises(RuntimeError):
            vpn.main(["kube", "work", "--", "exec", "-it", "pod", "--", "sh"])
        self.assertEqual(execute.call_args.args, ("/stub/kubectl", ["/stub/kubectl", "--context=development", "exec", "-it", "pod", "--", "sh"]))

    def test_default_route_is_fallback_and_host_route_is_exact(self):
        with patch.object(vpn, "run", return_value="destination: default\nflags: <UP,GATEWAY,DONE>\ninterface: utun9"):
            self.assertIsNone(vpn.exact_route("192.0.2.1"))
        with patch.object(vpn, "run", return_value="destination: 192.0.2.1\nflags: <UP,HOST,DONE>\ninterface: utun42"):
            self.assertEqual(vpn.exact_route("192.0.2.1"), "utun42")

    def test_pending_startup_cannot_pin_routes(self):
        profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1"], "", "")
        with patch.object(vpn, "add_route") as add, self.assertRaisesRegex(vpn.Error, "unfinished startup"):
            vpn.pin_routes(Path("/state"), profile, {"utun": None, "routes": []})
        add.assert_not_called()

    def test_private_state_rejects_symlink_and_nonprivate_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o755)
            link = root / "link"
            link.symlink_to(target)
            for path in [link, target]:
                with self.subTest(path=path), self.assertRaises(vpn.Error):
                    vpn.private_directory(path)

    def test_state_rejects_malformed_interface_and_ip(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            data = {"pid": 123, "binary": "/stub/openvpn", "started": "start", "utun": "../utun42", "routes": ["192.0.2.1"]}
            for key, value in [("utun", "../utun42"), ("routes", ["999.0.2.1"])]:
                data.update(utun="utun42", routes=["192.0.2.1"])
                data[key] = value
                (directory / "work.json").write_text(json.dumps(data))
                with patch.object(vpn, "private_file"), self.assertRaises(vpn.Error):
                    vpn.read_state(directory, "work")

    def test_recycled_pid_cannot_be_killed(self):
        state = {"pid": 123, "binary": "/stub/openvpn", "started": "old process", "utun": "utun42", "routes": ["192.0.2.1"]}
        values = {"uid": "0", "comm": "/stub/openvpn", "lstart": "new process",
                  "command": "/stub/openvpn --config /state/work.conf --daemon"}
        with patch.object(vpn, "process_value", side_effect=lambda pid, field: values[field]):
            self.assertFalse(vpn.owns_process(Path("/state"), "work", state))
        with patch.object(vpn, "read_state", return_value=state), \
                patch.object(vpn, "owns_process", return_value=False), \
                patch.object(vpn, "exact_route", return_value="utun42"), \
                patch.object(vpn.os, "kill") as kill, self.assertRaises(vpn.Error):
            vpn.down(Path("/state"), "work")
        kill.assert_not_called()

    def test_unfinished_journal_blocks_automatic_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state = {"pid": 123, "binary": "/stub/openvpn", "started": "start", "utun": "utun42",
                     "routes": [], "pending_routes": ["192.0.2.1"]}
            (directory / "work.json").write_text(json.dumps(state))
            with patch.object(vpn, "private_file"), self.assertRaisesRegex(vpn.Error, "inspect recorded intent"):
                vpn.read_state(directory, "work")

    def test_privilege_dropped_daemon_is_recorded_and_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1"], "", "")
            values = {"uid": "65534", "comm": "/stub/openvpn", "lstart": "started",
                      "command": "/stub/openvpn --config " + str(root / "work.conf") + " --daemon"}
            saved = []
            def launch(args):
                (root / "work.pid").write_text("123")
                (root / "work.log").write_text("Opened utun device utun42\nInitialization Sequence Completed\n")
            def save(directory, name, state):
                saved.append(json.loads(json.dumps(state)))
                (directory / (name + ".json")).write_text(json.dumps(state))
            with patch.object(vpn, "read_state", return_value=None), patch.object(vpn, "resolve_ips"), \
                    patch.object(vpn, "wrapper", return_value=root / "work.conf"), \
                    patch.object(vpn, "executable", return_value="/stub/openvpn"), patch.object(vpn, "run", side_effect=launch), \
                    patch.object(vpn, "private_file"), patch.object(vpn, "save_state", side_effect=save), \
                    patch.object(vpn, "process_value", side_effect=lambda pid, field: values[field]), \
                    patch.object(vpn.socket, "if_nametoindex", return_value=42), \
                    patch.object(vpn, "pin_routes"), contextlib.redirect_stdout(io.StringIO()):
                vpn.up(root, profile)
            self.assertEqual(saved[-1]["utun"], "utun42")
            self.assertEqual(saved[-1]["binary"], "/stub/openvpn")
            with patch.object(vpn, "process_value", side_effect=lambda pid, field: values[field]):
                self.assertTrue(vpn.owns_process(root, "work", saved[-1]))
                values["comm"] = "/other/openvpn"
                self.assertFalse(vpn.owns_process(root, "work", saved[-1]))
                values["comm"] = "/stub/openvpn"
                with patch.object(vpn, "read_state", return_value=saved[-1]), \
                        patch.object(vpn.os, "kill", side_effect=lambda pid, sig: values.update(command="")) as kill, \
                        contextlib.redirect_stdout(io.StringIO()):
                    vpn.down(root, "work")
                kill.assert_called_once_with(123, signal.SIGTERM)

    def test_wrapper_quotes_spaces_and_disables_all_openvpn_route_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "vpn configs" / "client.ovpn"
            base.parent.mkdir()
            base.touch()
            profile = vpn.Profile("work", base, ["192.0.2.1"], "", "")
            result = vpn.wrapper(root, profile).read_text()
            self.assertIn('config "' + str(base) + '"\n', result)
            self.assertIn("route-nopull\nroute-noexec\n", result)
            self.assertNotIn("persist-key", result)

    def test_failed_startup_signals_only_verified_daemon(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "work.pid").write_text("123")
            profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1"], "", "")
            with patch.object(vpn, "read_state", return_value=None), patch.object(vpn, "resolve_ips"), \
                    patch.object(vpn, "wrapper", return_value=root / "work.conf"), \
                    patch.object(vpn, "executable", return_value="/stub/openvpn"), \
                    patch.object(vpn, "run"), patch.object(vpn, "private_file"), \
                    patch.object(vpn, "save_state"), patch.object(vpn, "process_value", return_value="started"), \
                    patch.object(vpn, "owns_process", return_value=True), patch.object(vpn.time, "sleep"), \
                    patch.object(vpn.os, "kill") as kill, patch.object(Path, "unlink"), self.assertRaises(vpn.Error):
                vpn.up(root, profile)
            kill.assert_called_once_with(123, signal.SIGTERM)

    def test_startup_does_not_accept_initialization_from_before_a_reopen(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = vpn.Profile("work", Path("/client.ovpn"), ["192.0.2.1"], "", "")
            def launch(args):
                self.assertIn("--log-append", args)
                (root / "work.pid").write_text("123")
                (root / "work.log").write_text("Opened utun device utun42\nInitialization Sequence Completed\nSIGHUP received\nOpened utun device utun42\n")
            with patch.object(vpn, "read_state", return_value=None), patch.object(vpn, "resolve_ips"), \
                    patch.object(vpn, "wrapper", return_value=root / "work.conf"), \
                    patch.object(vpn, "executable", return_value="/stub/openvpn"), patch.object(vpn, "run", side_effect=launch), \
                    patch.object(vpn, "private_file"), patch.object(vpn, "save_state"), \
                    patch.object(vpn, "process_value", return_value="started"), patch.object(vpn, "owns_process", return_value=True), \
                    patch.object(vpn.time, "sleep"), patch.object(vpn.os, "kill") as kill, \
                    self.assertRaisesRegex(vpn.Error, "did not initialize"):
                vpn.up(root, profile)
            kill.assert_called_once_with(123, signal.SIGTERM)

    def test_launcher_works_via_symlink_with_system_path(self):
        launcher = Path(__file__).resolve().parents[1] / "bin/vpn-pin"
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "vpn pin"
            link.symlink_to(launcher)
            result = subprocess.run([str(link), "--help"], env={**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OpenVPN", result.stdout)


if __name__ == "__main__":
    unittest.main()
