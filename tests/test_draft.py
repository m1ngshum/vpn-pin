import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import create_draft as draft


class Draft(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.commit = "a" * 40
        archive = "vpn-pin-0.2.0.tar.gz"
        (self.directory / archive).write_bytes(b"fixture install archive")
        (self.directory / "vpn-pin.rb").write_text("fixture formula")
        receipt = {"version": "0.2.0", "commit": self.commit, "archive": archive,
                   "sha256": hashlib.sha256((self.directory / archive).read_bytes()).hexdigest()}
        (self.directory / "RELEASE.json").write_text(json.dumps(receipt))
        names = [archive, "vpn-pin.rb", "RELEASE.json"]
        (self.directory / "SHA256SUMS").write_text("".join(hashlib.sha256((self.directory / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in names))
        source = patch.object(draft, "git", return_value=b"0.2.0\n")
        source.start()
        self.addCleanup(source.stop)

    def test_mismatched_existing_tag_prevents_any_release_mutation(self):
        with patch.object(draft, "tag_commit", return_value="b" * 40), \
                patch.object(draft.subprocess, "check_call") as call, self.assertRaisesRegex(ValueError, "different commit"):
            draft.create_draft("example/project", self.directory, self.commit)
        call.assert_not_called()

    def test_new_tag_is_created_verified_and_only_a_draft_is_created(self):
        with patch.object(draft, "tag_commit", side_effect=[None, self.commit, self.commit]) as tag, \
                patch.object(draft.subprocess, "check_call") as call:
            draft.create_draft("example/project", self.directory, self.commit)
        self.assertIn("sha=" + self.commit, call.call_args_list[0].args[0])
        command = call.call_args_list[1].args[0]
        self.assertIn("--verify-tag", command)
        self.assertIn("--draft", command)
        self.assertEqual(tag.call_count, 3)

    def test_tag_race_before_draft_stops_and_does_not_publish(self):
        with patch.object(draft, "tag_commit", side_effect=[self.commit, "b" * 40]), \
                patch.object(draft.subprocess, "check_call") as call, self.assertRaisesRegex(ValueError, "changed before"):
            draft.create_draft("example/project", self.directory, self.commit)
        call.assert_not_called()

    def test_tampered_asset_fails_before_remote_calls(self):
        (self.directory / "vpn-pin.rb").write_text("changed asset")
        with patch.object(draft, "tag_commit") as tag, self.assertRaisesRegex(ValueError, "checksum"):
            draft.create_draft("example/project", self.directory, self.commit)
        tag.assert_not_called()

    def test_annotated_tags_are_peeled_and_remote_errors_propagate(self):
        refs = "b" * 40 + "\trefs/tags/v0.2.0\n" + self.commit + "\trefs/tags/v0.2.0^{}\n"
        with patch.object(draft.subprocess, "check_output", return_value=refs):
            self.assertEqual(draft.tag_commit("example/project", "v0.2.0"), self.commit)
        with patch.object(draft.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, [])), self.assertRaises(subprocess.CalledProcessError):
            draft.tag_commit("example/project", "v0.2.0")


if __name__ == "__main__":
    unittest.main()
