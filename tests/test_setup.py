import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class Setup(unittest.TestCase):
    def test_install_idempotence_and_conflicts(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vpn pin ") as temporary:
            prefix = Path(temporary) / "local tools"
            environment = {**os.environ, "PREFIX": str(prefix)}

            def install():
                return subprocess.run([str(root / "setup.sh")], env=environment,
                                      text=True, capture_output=True)

            target = prefix / "bin/vpn-pin"
            self.assertEqual(install().returncode, 0)
            self.assertEqual(target.resolve(), root / "bin/vpn-pin")
            self.assertEqual(install().returncode, 0)
            help_result = subprocess.run([str(target), "--help"], text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            target.unlink()
            target.write_text("existing tool")
            self.assertNotEqual(install().returncode, 0)
            self.assertEqual(target.read_text(), "existing tool")
            target.unlink()
            target.symlink_to(prefix / "absent")
            self.assertNotEqual(install().returncode, 0)
            self.assertEqual(os.readlink(target), str(prefix / "absent"))


if __name__ == "__main__":
    unittest.main()
