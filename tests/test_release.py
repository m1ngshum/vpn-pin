import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_release import build_release


class Release(unittest.TestCase):
    def test_reproducible_committed_archive_checksums_and_install(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vpn pin release ") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            for name in ("bin", "lib", "docs", "examples"):
                shutil.copytree(source / name, repo / name, ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("VERSION", "setup.sh", "README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md"):
                shutil.copy2(source / name, repo / name)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)
            git("init")
            git("config", "core.hooksPath", "/dev/null")
            git("add", ".")
            git("-c", "user.name=Test contributors", "-c", "user.email=contributors@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "Release fixture")
            expected_version = (repo / "VERSION").read_text().strip()
            (repo / "VERSION").write_text("99.0.0\n")
            (repo / "private.key").write_text("untracked fixture must not be packaged")
            first, second = root / "first", root / "second"
            commit, version = build_release(repo, "HEAD", first)
            original = gzip.GzipFile
            def changed_default(*args, compresslevel=1, **kwargs):
                return original(*args, compresslevel=compresslevel, **kwargs)
            with patch("build_release.gzip.GzipFile", side_effect=changed_default):
                build_release(repo, commit, second)
            self.assertEqual(version, expected_version)
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            receipt = json.loads((first / "RELEASE.json").read_text())
            archive = first / receipt["archive"]
            self.assertEqual(receipt["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            for line in (first / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256((first / name).read_bytes()).hexdigest())
            formula = (first / "vpn-pin.rb").read_text()
            self.assertIn('libexec.install "bin", "lib", "VERSION"', formula)
            self.assertIn(receipt["sha256"], formula)
            self.assertIn("/releases/download/v" + version + "/" + archive.name, formula)
            with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.read_bytes()))) as tar:
                self.assertTrue(all(not member.issym() and ".." not in Path(member.name).parts and not Path(member.name).is_absolute() for member in tar))
                self.assertFalse(any(member.name.endswith("private.key") for member in tar))
                tar.extractall(root / "extracted")
            extracted = root / "extracted" / ("vpn-pin-" + version)
            self.assertTrue((extracted / "examples/profiles.example").is_file())
            prefix = root / "installed tools"
            subprocess.check_call([str(extracted / "setup.sh")], env={**os.environ, "PREFIX": str(prefix)}, stdout=subprocess.DEVNULL)
            installed = prefix / "bin/vpn-pin"
            self.assertIn(version, subprocess.check_output([str(installed), "--version"], text=True))
            self.assertIn("watch", subprocess.check_output([str(installed), "--help"], text=True))
            if shutil.which("ruby"):
                subprocess.check_call(["ruby", "-c", str(first / "vpn-pin.rb")], stdout=subprocess.DEVNULL)

    def test_missing_runtime_files_fail_before_artifact_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.check_call(["git", "init", "-q", str(repo)])
            (repo / "VERSION").write_text("0.2.0\n")
            subprocess.check_call(["git", "-C", str(repo), "add", "."])
            subprocess.check_call(["git", "-C", str(repo), "-c", "user.name=Test contributors", "-c", "user.email=contributors@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Incomplete fixture"])
            with self.assertRaisesRegex(ValueError, "missing required"):
                build_release(repo, "HEAD", repo / "out")
            self.assertFalse((repo / "out").exists())


if __name__ == "__main__":
    unittest.main()
