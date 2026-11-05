import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import vpn_pin as vpn


class RestartOwned(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        self.old = {"pid": 123, "binary": "/fixture/openvpn", "started": "old", "utun": "utun42",
                    "ifindex": 42, "log_guard": {"size": 1}, "routes": ["192.0.2.2"]}
        self.new = dict(self.old, pid=124, started="new", utun="utun43", ifindex=43,
                        log_guard={"size": 2}, routes=["192.0.2.1"])
        self.started = False
        def mock(name, **kwargs):
            return self.stack.enter_context(patch.object(vpn, name, **kwargs))
        self.read = mock("read_state", side_effect=[copy.deepcopy(self.old), copy.deepcopy(self.new)])
        self.owns = mock("owns_process", return_value=True)
        self.life = mock("require_lifetime", side_effect=[vpn.ContinuityError("closed"), None])
        self.profiles = mock("load_profiles", return_value={"work": self.profile})
        self.client = mock("client_identity", return_value="stamp")
        self.route = mock("exact_route", side_effect=lambda ip: "utun43" if self.started else None)
        self.real_down = vpn.down
        self.down = mock("down")
        def startup(*args):
            self.started = True
            return copy.deepcopy(self.new)
        self.up = mock("up", side_effect=startup)

    def restart(self):
        return vpn.restart_owned(Path("/state"), Path("/config"), self.profile,
                                 copy.deepcopy(self.old), ["192.0.2.1"], "stamp")

    def untouched(self):
        self.down.assert_not_called()
        self.up.assert_not_called()

    def test_fresh_session_uses_pre_resolved_targets_and_owned_expected_stop(self):
        self.assertEqual(self.restart(), self.new)
        self.down.assert_called_once_with(Path("/state"), "work", expected=self.old)
        self.up.assert_called_once_with(Path("/state"), self.profile, ["192.0.2.1"])

    def test_changed_state_or_process_is_never_signaled(self):
        for changed in (None, dict(self.old, pid=999), dict(self.old, routes=[])):
            with self.subTest(changed=changed):
                self.read.side_effect = None
                self.read.return_value = changed
                with self.assertRaises(vpn.Error):
                    self.restart()
                self.untouched()
        self.read.return_value = self.old
        self.owns.return_value = False
        with self.assertRaises(vpn.Error):
            self.restart()
        self.untouched()

    def test_only_verified_continuity_loss_can_trigger_restart(self):
        for error in (vpn.Error("log changed"), vpn.Error("termination requested"), OSError("inspection failed")):
            with self.subTest(error=error):
                self.read.side_effect = None
                self.read.return_value = self.old
                self.life.side_effect = error
                with self.assertRaises((vpn.Error, OSError)):
                    self.restart()
                self.untouched()
        self.life.side_effect = None
        with self.assertRaisesRegex(vpn.Error, "requires verified"):
            self.restart()
        self.untouched()

    def test_profile_or_client_change_blocks_restart(self):
        changed = vpn.Profile("work", self.profile.ovpn, ["192.0.2.9"], "", "")
        for profile, stamp in [(None, "stamp"), (changed, "stamp"), (self.profile, "changed")]:
            with self.subTest(profile=profile, stamp=stamp):
                self.read.side_effect = None
                self.read.return_value = self.old
                self.life.side_effect = vpn.ContinuityError("closed")
                self.profiles.return_value = {"work": profile}
                self.client.return_value = stamp
                with self.assertRaisesRegex(vpn.Error, "configuration changed"):
                    self.restart()
                self.untouched()

    def test_remaining_route_or_failed_lookup_blocks_restart(self):
        for route, error in [("utun42", None), ("utun9", None), (None, vpn.Error("lookup failed"))]:
            with self.subTest(route=route, error=error):
                self.read.side_effect = None
                self.read.return_value = self.old
                self.life.side_effect = vpn.ContinuityError("closed")
                self.route.side_effect = error
                self.route.return_value = route
                with self.assertRaises(vpn.Error):
                    self.restart()
                self.untouched()

    def test_route_appearing_after_stop_blocks_startup(self):
        self.route.side_effect = [None, None, "utun9"]
        with self.assertRaisesRegex(vpn.Error, "routes remain"):
            self.restart()
        self.down.assert_called_once()
        self.up.assert_not_called()

    def test_client_changed_during_stop_blocks_startup(self):
        self.client.side_effect = ["stamp", "changed"]
        with self.assertRaisesRegex(vpn.Error, "configuration changed"):
            self.restart()
        self.down.assert_called_once()
        self.up.assert_not_called()

    def test_profile_changed_during_stop_blocks_startup(self):
        self.profiles.side_effect = [{"work": self.profile}, {}]
        with self.assertRaisesRegex(vpn.Error, "configuration changed"):
            self.restart()
        self.down.assert_called_once()
        self.up.assert_not_called()

    def test_failed_stop_or_start_is_not_retried(self):
        self.down.side_effect = vpn.Error("stop refused")
        with self.assertRaisesRegex(vpn.Error, "stop refused"):
            self.restart()
        self.up.assert_not_called()
        self.down.side_effect = None
        self.down.reset_mock()
        self.read.side_effect = [self.old]
        self.life.side_effect = [vpn.ContinuityError("closed")]
        self.up.side_effect = vpn.Error("startup failed")
        with self.assertRaisesRegex(vpn.Error, "startup failed"):
            self.restart()
        self.down.assert_called_once()
        self.up.assert_called_once()

    def test_interrupted_start_does_not_adopt_state_or_retry(self):
        self.up.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.restart()
        self.down.assert_called_once()
        self.up.assert_called_once()
        self.assertEqual(self.read.call_count, 1)

    def test_replacement_state_after_start_is_not_adopted(self):
        self.read.side_effect = [self.old, dict(self.new, pid=999)]
        with self.assertRaisesRegex(vpn.Error, "not owned"):
            self.restart()

    def test_new_process_lifetime_and_routes_must_be_verified(self):
        self.life.side_effect = [vpn.ContinuityError("closed"), vpn.Error("new log changed")]
        with self.assertRaisesRegex(vpn.Error, "new log changed"):
            self.restart()

    def test_unowned_new_process_is_not_adopted(self):
        self.owns.side_effect = [True, False]
        with self.assertRaisesRegex(vpn.Error, "not owned"):
            self.restart()

    def test_foreign_route_after_start_prevents_reporting_recovery(self):
        self.route.side_effect = [None, None, None, None, "utun9"]
        with self.assertRaisesRegex(vpn.Error, "routes are not verified"):
            self.restart()

    def test_real_stop_ownership_loss_cannot_clear_state_or_start_again(self):
        self.down.side_effect = self.real_down
        self.read.side_effect = lambda *args: copy.deepcopy(self.new if self.started else self.old)
        self.owns.side_effect = [True, False, True]
        with patch.object(vpn, "clean_stale") as stale, patch.object(vpn.os, "kill") as kill:
            with self.assertRaisesRegex(vpn.Error, "ownership lost"):
                self.restart()
        stale.assert_not_called()
        kill.assert_not_called()
        self.up.assert_not_called()

    def test_real_stop_rechecks_log_after_its_own_confirmed_route_pruning(self):
        for message in ("termination requested", "log was replaced"):
            with self.subTest(message=message):
                disk = [copy.deepcopy(self.old)]
                self.read.side_effect = lambda *args: copy.deepcopy(disk[0])
                self.owns.side_effect = None
                self.owns.return_value = True
                self.life.side_effect = vpn.ContinuityError("closed")
                self.down.side_effect = self.real_down
                self.down.reset_mock()
                with patch.object(vpn, "checked_log_tail", side_effect=[b"closed", vpn.Error(message)]), \
                        patch.object(vpn, "save_state", side_effect=lambda d, n, state: disk.__setitem__(0, copy.deepcopy(state))), \
                        patch.object(vpn.os, "kill", side_effect=AssertionError("must not signal")) as kill:
                    with self.assertRaisesRegex(vpn.Error, message):
                        self.restart()
                self.assertEqual(disk[0]["routes"], [])
                self.down.assert_called_once()
                kill.assert_not_called()
                self.up.assert_not_called()

    def test_real_stop_rejects_late_state_replacement_before_signaling(self):
        self.old["routes"] = []
        self.down.side_effect = self.real_down
        self.read.side_effect = [copy.deepcopy(self.old), copy.deepcopy(self.old), dict(self.old, pid=999)]
        with patch.object(vpn, "checked_log_tail", return_value=b"closed"), \
                patch.object(vpn.os, "kill") as kill, self.assertRaisesRegex(vpn.Error, "state changed during"):
            self.restart()
        kill.assert_not_called()
        self.up.assert_not_called()


class AutoWatch(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        self.old = {"pid": 123, "binary": "/fixture/openvpn", "started": "old", "utun": "utun42",
                    "ifindex": 42, "log_guard": {"size": 1}, "routes": ["192.0.2.1"]}
        self.new = dict(self.old, pid=124, started="new", utun="utun43", ifindex=43)
        self.locked = []
        @contextlib.contextmanager
        def directory(uid):
            self.locked.append(True)
            try:
                yield Path("/state")
            finally:
                self.locked.pop()
        def mock(name, **kwargs):
            return self.stack.enter_context(patch.object(vpn, name, **kwargs))
        mock("state_directory", side_effect=directory)
        self.profiles = mock("load_profiles", return_value={"work": self.profile})
        mock("client_identity", return_value="stamp")
        self.read = mock("read_state", side_effect=[self.old, self.old, self.new])
        mock("owns_process", return_value=True)
        self.life = mock("require_lifetime", side_effect=[None, vpn.ContinuityError("closed"), None])
        def restart(*args):
            self.assertTrue(self.locked)
            return self.new
        self.restart = mock("restart_owned", side_effect=restart)
        self.route = mock("exact_route", return_value="utun43")
        def resolve(profile):
            self.assertFalse(self.locked)
            return ["192.0.2.1"]
        mock("resolve_ips", side_effect=resolve)
        def sleep(delay):
            self.assertFalse(self.locked)
            self.ticks += 1
            if self.ticks == 2:
                raise KeyboardInterrupt()
        self.ticks = 0
        self.stack.enter_context(patch.object(vpn.time, "sleep", side_effect=sleep))

    def test_opt_in_restarts_once_then_tracks_only_its_new_session(self):
        with self.assertRaises(KeyboardInterrupt):
            vpn.watch(Path("/config"), "work", 5, True)
        self.restart.assert_called_once()
        self.assertEqual(self.read.call_count, 3)

    def test_second_loss_exhausts_budget_without_another_restart(self):
        self.life.side_effect = [None, vpn.ContinuityError("closed"), vpn.ContinuityError("closed again")]
        with self.assertRaisesRegex(vpn.Error, "attempt exhausted"):
            vpn.watch(Path("/config"), "work", 5, True)
        self.restart.assert_called_once()

    def test_default_watch_does_not_restart(self):
        with self.assertRaises(vpn.ContinuityError):
            vpn.watch(Path("/config"), "work", 5)
        self.restart.assert_not_called()

    def test_foreign_state_is_not_adopted_in_opt_in_mode(self):
        self.read.side_effect = [self.old, dict(self.old, pid=999)]
        with self.assertRaisesRegex(vpn.Error, "watched tunnel changed"):
            vpn.watch(Path("/config"), "work", 5, True)
        self.restart.assert_not_called()

    def test_failed_restart_exits_without_retry(self):
        self.restart.side_effect = vpn.Error("startup failed; state retained")
        with self.assertRaisesRegex(vpn.Error, "inspect /state/work.json"):
            vpn.watch(Path("/config"), "work", 5, True)
        self.restart.assert_called_once()
        self.assertEqual(self.ticks, 0)

    def test_changed_profile_blocks_automatic_restart(self):
        changed = vpn.Profile("work", self.profile.ovpn, ["192.0.2.9"], "", "")
        self.profiles.side_effect = [{"work": self.profile}, {"work": changed}]
        with self.assertRaisesRegex(vpn.Error, "profile changed"):
            vpn.watch(Path("/config"), "work", 5, True)
        self.restart.assert_not_called()


class RecoveryGuards(unittest.TestCase):
    def test_cli_forwards_opt_in_flag_without_starting_a_tunnel(self):
        profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        with patch.object(vpn.sys, "platform", "darwin"), patch.object(vpn.os, "geteuid", return_value=0), \
                patch.object(vpn, "load_profiles", return_value={"work": profile}), patch.object(vpn.os, "umask"), \
                patch.object(vpn, "watch") as watch:
            vpn.main(["--config", "/fixture/profiles.conf", "watch", "work", "--interval", "5", "--auto-reconnect"])
        watch.assert_called_once_with(Path("/fixture/profiles.conf"), "work", 5.0, True)

    def test_client_metadata_changes_and_non_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "client.ovpn"
            profile = vpn.Profile("work", path, ["192.0.2.1"], "", "")
            path.write_text("fixture")
            original = vpn.client_identity(profile)
            path.write_text("changed fixture")
            self.assertNotEqual(vpn.client_identity(profile), original)
            path.unlink()
            with self.assertRaises(vpn.Error):
                vpn.client_identity(profile)
            path.mkdir()
            with self.assertRaisesRegex(vpn.Error, "regular file"):
                vpn.client_identity(profile)

    def test_termination_or_log_corruption_is_not_continuity_loss(self):
        base = b"initial log\n"
        guard = {"device": 1, "inode": 2, "size": len(base), "sha256": hashlib.sha256(base).hexdigest()}
        state = {"utun": "utun42", "ifindex": 42, "log_guard": guard}
        for data in (base + b"SIGTERM received\n", base + b"SIGINT received\n", b"corrupted\nClosing TUN/TAP interface\n"):
            with self.subTest(data=data), patch.object(vpn, "log_snapshot", return_value=(data, guard)), \
                    patch.object(vpn, "require_interface", side_effect=vpn.ContinuityError("missing")):
                with self.assertRaises(vpn.Error) as raised:
                    vpn.require_lifetime(Path("/state"), "work", state)
                self.assertNotIsInstance(raised.exception, vpn.ContinuityError)

    def test_unknown_interface_error_is_not_reconnect_permission(self):
        state = {"utun": "utun42", "ifindex": 42}
        with patch.object(vpn.socket, "if_nametoindex", side_effect=OSError("inspection failed")), \
                patch.object(vpn.socket, "if_nameindex", return_value=[(42, "utun42")]):
            with self.assertRaises(vpn.Error) as raised:
                vpn.require_interface(state)
            self.assertNotIsInstance(raised.exception, vpn.ContinuityError)
        with patch.object(vpn.socket, "if_nametoindex", side_effect=OSError("missing")), \
                patch.object(vpn.socket, "if_nameindex", return_value=[]), self.assertRaises(vpn.ContinuityError):
            vpn.require_interface(state)

    def test_expected_stop_rejects_replacement_state_before_signaling(self):
        expected = {"pid": 123, "routes": []}
        with patch.object(vpn, "read_state", return_value={"pid": 999, "routes": []}), \
                patch.object(vpn.os, "kill") as kill, self.assertRaisesRegex(vpn.Error, "state changed"):
            vpn.down(Path("/state"), "work", expected=expected)
        kill.assert_not_called()

    def test_pre_resolved_startup_never_reuses_existing_state(self):
        profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        with patch.object(vpn, "read_state", return_value={"pid": 999}), \
                patch.object(vpn, "owns_process") as owns, self.assertRaisesRegex(vpn.Error, "state appeared"):
            vpn.up(Path("/state"), profile, ["192.0.2.1"])
        owns.assert_not_called()

    def test_failed_fresh_start_preserves_unfinished_state_without_dns(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
            def launch(args):
                (directory / "work.pid").write_text("123")
                (directory / "work.log").write_text("Opened utun device utun43\n")
            with patch.object(vpn, "private_file"), patch.object(vpn, "wrapper", return_value=directory / "work.conf"), \
                    patch.object(vpn, "executable", return_value="/fixture/openvpn"), patch.object(vpn, "run", side_effect=launch), \
                    patch.object(vpn, "process_value", return_value="started"), patch.object(vpn, "owns_process", return_value=True), \
                    patch.object(vpn, "resolve_ips") as resolve, patch.object(vpn.time, "sleep"), \
                    patch.object(vpn.os, "kill") as kill, self.assertRaisesRegex(vpn.Error, "did not initialize"):
                vpn.up(directory, profile, ["192.0.2.1"])
            resolve.assert_not_called()
            kill.assert_called_once_with(123, signal.SIGTERM)
            state = json.loads((directory / "work.json").read_text())
            self.assertIsNone(state["utun"])
            self.assertEqual(state["routes"], [])


if __name__ == "__main__":
    unittest.main()
