import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import vpn_pin as vpn


class Commands(unittest.TestCase):
    def setUp(self):
        self.profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        self.state = {"pid": 123, "binary": "/fixture/openvpn", "started": "started", "utun": "utun42", "ifindex": 42, "routes": ["192.0.2.2"]}
        interface = patch.object(vpn.socket, "if_nametoindex", return_value=42)
        interface.start()
        self.addCleanup(interface.stop)
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_plan_is_read_only_and_shares_add_remove_conflict_calculation(self):
        with patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), \
                patch.object(vpn, "exact_route", side_effect=lambda ip: "utun42" if ip.endswith("2") else None), \
                patch.object(vpn, "read_state", return_value=self.state), patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "save_state") as save, patch.object(vpn, "add_route") as add, patch.object(vpn, "remove_route") as remove:
            vpn.show_plan(Path("/state"), self.profile)
            self.assertIn("ADD 192.0.2.1", self.output.getvalue())
            self.assertIn("REMOVE 192.0.2.2", self.output.getvalue())
            save.assert_not_called()
            add.assert_not_called()
            remove.assert_not_called()
        with patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), \
                patch.object(vpn, "exact_route", return_value="utun9"), self.assertRaisesRegex(vpn.Error, "conflicts"):
            vpn.show_plan(None, self.profile)

    def test_plan_with_no_state_does_not_create_runtime_directory(self):
        with patch.object(vpn, "private_directory", side_effect=FileNotFoundError) as directory:
            self.assertIsNone(vpn.snapshot_directory(501))
        self.assertFalse(directory.call_args.kwargs["create"])

    def test_watch_releases_lock_before_sleep_and_rereads_state(self):
        locked = []
        @contextlib.contextmanager
        def directory(uid):
            locked.append(True)
            try:
                yield Path("/state")
            finally:
                locked.pop()
        ticks = []
        def sleep(delay):
            self.assertFalse(locked)
            ticks.append(delay)
            if len(ticks) == 2:
                raise KeyboardInterrupt()
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), patch.object(vpn, "state_directory", side_effect=directory), \
                patch.object(vpn, "read_state", return_value=self.state) as read, patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "exact_route", return_value=None), patch.object(vpn, "pin_routes") as pin, \
                patch.object(vpn.time, "sleep", side_effect=sleep), self.assertRaises(KeyboardInterrupt):
            vpn.watch(Path("/config"), "work", 5)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(pin.call_count, 2)

    def test_watch_dns_failure_preserves_routes_and_backs_off_without_lock(self):
        @contextlib.contextmanager
        def state_directory(uid):
            yield Path("/state")
        with patch.object(vpn, "read_state", return_value=self.state), patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "resolve_ips", side_effect=vpn.ResolutionError("DNS unavailable")), \
                patch.object(vpn, "state_directory", side_effect=state_directory) as directory, patch.object(vpn, "pin_routes") as pin, \
                patch.object(vpn.time, "sleep", side_effect=[None, None, KeyboardInterrupt()]) as sleep, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            vpn.watch(Path("/config"), "work", 30)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 60, 120])
        self.assertEqual(directory.call_count, 1)
        pin.assert_not_called()

    def test_watch_stops_when_process_identity_is_stale(self):
        @contextlib.contextmanager
        def directory(uid):
            yield Path("/state")
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), patch.object(vpn, "state_directory", side_effect=directory), \
                patch.object(vpn, "read_state", return_value=self.state), patch.object(vpn, "owns_process", return_value=False), \
                patch.object(vpn, "pin_routes") as pin, self.assertRaisesRegex(vpn.Error, "owned tunnel"):
            vpn.watch(Path("/config"), "work", 5)
        pin.assert_not_called()

    def test_watch_rejects_replacement_process_instead_of_adopting_it(self):
        @contextlib.contextmanager
        def directory(uid):
            yield Path("/state")
        replacement = dict(self.state, pid=456, started="replacement")
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), patch.object(vpn, "state_directory", side_effect=directory), \
                patch.object(vpn, "read_state", side_effect=[self.state, self.state, replacement]), patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "exact_route", return_value=None), patch.object(vpn, "pin_routes") as pin, \
                patch.object(vpn.time, "sleep"), self.assertRaisesRegex(vpn.Error, "tunnel changed"):
            vpn.watch(Path("/config"), "work", 5)
        self.assertEqual(pin.call_count, 1)

    def test_watch_rejects_same_name_interface_recreation(self):
        @contextlib.contextmanager
        def directory(uid):
            yield Path("/state")
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), patch.object(vpn, "state_directory", side_effect=directory), \
                patch.object(vpn, "read_state", return_value=self.state), patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn.socket, "if_nametoindex", side_effect=[42, 99]), \
                patch.object(vpn, "pin_routes") as pin, self.assertRaisesRegex(vpn.Error, "interface identity changed"):
            vpn.watch(Path("/config"), "work", 5)
        pin.assert_not_called()

    def test_watch_checks_initial_identity_before_retrying_dns(self):
        @contextlib.contextmanager
        def directory(uid):
            yield Path("/state")
        with patch.object(vpn, "state_directory", side_effect=directory), patch.object(vpn, "read_state", return_value=None), \
                patch.object(vpn, "resolve_ips") as resolve, self.assertRaisesRegex(vpn.Error, "owned tunnel"):
            vpn.watch(Path("/config"), "work", 5)
        resolve.assert_not_called()

    def test_dns_subprocess_is_bounded_and_failure_is_retryable(self):
        result = subprocess.CompletedProcess([], 0, '["192.0.2.1"]', "")
        with patch.object(vpn.subprocess, "run", return_value=result) as run:
            self.assertEqual(vpn.lookup_ipv4("api.example.test"), ["192.0.2.1"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        with patch.object(vpn.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 10)), self.assertRaises(vpn.ResolutionError):
            vpn.lookup_ipv4("api.example.test")

    def test_doctor_probe_is_opt_in_bounded_and_socket_is_closed(self):
        self.state["routes"] = ["192.0.2.1"]
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "executable", return_value="/fixture/openvpn"), patch.object(vpn, "run", return_value="default route"), \
                patch.object(vpn.os, "geteuid", return_value=0), patch.object(vpn, "snapshot_directory", return_value=Path("/state")), \
                patch.object(vpn, "read_state", return_value=self.state), patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "resolve_ips", return_value=["192.0.2.1"]), patch.object(vpn, "exact_route", return_value="utun42"), \
                patch.object(Path, "is_file", return_value=True), patch.object(vpn.socket, "create_connection") as connect:
            vpn.doctor(Path("/config"), "work")
            connect.assert_not_called()
            vpn.doctor(Path("/config"), "work", 443)
            connect.assert_called_once_with(("192.0.2.1", 443), timeout=2)
            connect.return_value.__exit__.assert_called_once()
            with patch.object(vpn, "exact_route", return_value="utun9"), self.assertRaisesRegex(vpn.Error, "probe skipped"):
                vpn.doctor(Path("/config"), "work", 443)
            self.assertEqual(connect.call_count, 1)

    def test_profile_add_preserves_comments_permissions_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profiles.conf"
            path.write_text("# user comment\n")
            path.chmod(0o640)
            vpn.add_profile(path, "work", "/fixture/vpn files/client.ovpn", ["192.0.2.1"])
            original = path.read_bytes()
            self.assertTrue(original.startswith(b"# user comment\n"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            with self.assertRaisesRegex(vpn.Error, "duplicate"):
                vpn.add_profile(path, "work", "/fixture/client.ovpn", ["192.0.2.2"])
            self.assertEqual(path.read_bytes(), original)

    def test_profile_add_refuses_unsafe_destinations_and_concurrent_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_text("# existing\n")
            link = root / "profiles.conf"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                vpn.add_profile(link, "work", "/fixture/client.ovpn", ["192.0.2.1"])
            link.unlink()
            os.link(source, link)
            with self.assertRaisesRegex(vpn.Error, "unsafe profiles"):
                vpn.add_profile(link, "work", "/fixture/client.ovpn", ["192.0.2.1"])
            link.unlink()
            source.chmod(0o666)
            with self.assertRaises(vpn.Error):
                vpn.add_profile(source, "work", "/fixture/client.ovpn", ["192.0.2.1"])
            source.chmod(0o600)
            replace = vpn.os.fsync
            def concurrent(descriptor):
                replace(descriptor)
                source.write_text("# concurrent edit\n")
            with patch.object(vpn.os, "fsync", side_effect=concurrent), self.assertRaisesRegex(vpn.Error, "concurrently"):
                vpn.add_profile(source, "work", "/fixture/client.ovpn", ["192.0.2.1"])
            self.assertEqual(source.read_text(), "# concurrent edit\n")

    def test_new_profile_file_is_private_and_invalid_input_leaves_no_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "new" / "profiles.conf"
            with self.assertRaises(vpn.Error):
                vpn.add_profile(path, "bad|name", "/fixture/client.ovpn", ["192.0.2.1"])
            self.assertFalse(path.exists())
            vpn.add_profile(path, "work", "/fixture/client.ovpn", ["192.0.2.1"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_watch_interval_is_validated_before_privilege_escalation(self):
        with patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn.sys, "platform", "darwin"), patch.object(vpn.os, "execv") as execute:
            for interval in ("0", "nan", "3601"):
                with self.subTest(interval=interval), self.assertRaisesRegex(vpn.Error, "interval"):
                    vpn.main(["watch", "work", "--interval", interval])
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
