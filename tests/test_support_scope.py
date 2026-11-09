import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import vpn_pin as vpn


class SupportScope(unittest.TestCase):
    def setUp(self):
        self.profile = vpn.Profile("work", Path("/fixture/client.ovpn"), ["192.0.2.1"], "", "")
        self.state = {"pid": 123, "utun": "utun42", "routes": ["192.0.2.1"]}
        self.output = io.StringIO()

    @contextlib.contextmanager
    def directory(self, *args):
        yield Path("/fixture/state")

    def test_help_discloses_scope_without_profiles_or_privilege(self):
        for command in ([], ["up"], ["reconnect"], ["watch"], ["api-pf"]):
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()) as output, \
                    patch.object(vpn, "load_profiles", side_effect=AssertionError("private profiles read")), \
                    patch.object(vpn, "run", side_effect=AssertionError("external command")), \
                    patch.object(vpn.os, "execv", side_effect=AssertionError("privilege requested")):
                with self.assertRaises(SystemExit) as exit:
                    vpn.main(command + ["--help"])
                self.assertEqual(exit.exception.code, 0)
                text = " ".join(output.getvalue().lower().split())
                self.assertIn("awake-only support", text)
                self.assertIn("sleep/wake recovery", text)
                self.assertIn("unsupported", text)
                self.assertIn("down profile", text)

    def test_shared_up_warns_before_tunnel_mutation(self):
        def wrapper(*args):
            self.assertIn(vpn.AWAKE_ONLY, self.output.getvalue())
            raise vpn.Error("stop before tunnel mutation")
        with contextlib.redirect_stderr(self.output), patch.object(vpn.api_pf, "guard"), \
                patch.object(vpn, "read_state", return_value=None), \
                patch.object(vpn, "resolve_ips", return_value=self.profile.targets), \
                patch.object(vpn, "wrapper", side_effect=wrapper), \
                patch.object(vpn, "run") as run, self.assertRaisesRegex(vpn.Error, "stop before"):
            vpn.up(Path("/fixture/state"), self.profile)
        run.assert_not_called()

    def test_watch_warns_before_refresh_loop(self):
        def profiles(*args):
            self.assertIn(vpn.AWAKE_ONLY, self.output.getvalue())
            raise vpn.Error("stop before refresh")
        with contextlib.redirect_stderr(self.output), contextlib.redirect_stdout(io.StringIO()), \
                patch.object(vpn.api_pf, "guard"), \
                patch.object(vpn, "human", return_value=SimpleNamespace(pw_uid=501)), \
                patch.object(vpn, "state_directory", side_effect=self.directory), \
                patch.object(vpn, "read_state", return_value=self.state), \
                patch.object(vpn, "owns_process", return_value=True), \
                patch.object(vpn, "require_lifetime"), patch.object(vpn, "load_profiles", side_effect=profiles), \
                patch.object(vpn, "pin_routes") as pin, self.assertRaisesRegex(vpn.Error, "stop before"):
            vpn.watch(Path("/fixture/profiles"), "work", 5)
        pin.assert_not_called()

    def test_pf_warns_before_grant(self):
        def enable(*args):
            self.assertIn(vpn.AWAKE_ONLY, self.output.getvalue())
            raise vpn.Error("stop before grant")
        with contextlib.redirect_stderr(self.output), patch.object(vpn.signal, "signal"), \
                patch.object(vpn.signal, "pthread_sigmask", return_value=set()), \
                patch.object(vpn, "load_profiles", return_value={"work": self.profile}), \
                patch.object(vpn, "client_identity", return_value=("fixture",)), \
                patch.object(vpn, "resolve_ips", return_value=self.profile.targets), \
                patch.object(vpn, "human", return_value=SimpleNamespace(pw_uid=501)), \
                patch.object(vpn, "state_directory", side_effect=self.directory), \
                patch.object(vpn, "read_state", return_value=self.state), \
                patch.object(vpn, "require_pf_session"), patch.object(vpn.api_pf, "enable", side_effect=enable), \
                patch.object(vpn.api_pf, "revoke") as revoke, self.assertRaisesRegex(vpn.Error, "stop before"):
            vpn.supervise_api_pf(Path("/fixture/profiles"), "work")
        revoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
