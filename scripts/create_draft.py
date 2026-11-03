#!/usr/bin/env python3
"""Create a draft only when assets and the remote version tag match the chosen commit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from build_release import git


def tag_commit(repo, tag):
    ref = "refs/tags/" + tag
    output = subprocess.check_output(["git", "ls-remote", "https://github.com/" + repo + ".git", ref, ref + "^{}"], text=True)
    refs = {name: sha for sha, name in (line.split() for line in output.splitlines())}
    return refs.get(ref + "^{}", refs.get(ref))


def create_draft(repo, directory, commit):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("supply a GitHub owner/repository and exact commit SHA")
    receipt = json.loads((directory / "RELEASE.json").read_text())
    version = git(Path(__file__).resolve().parents[1], "show", commit + ":VERSION").decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?", version) or receipt["version"] != version or receipt["commit"] != commit:
        raise ValueError("release receipt does not match the chosen source commit/version")
    archive = "vpn-pin-" + version + ".tar.gz"
    if receipt["archive"] != archive or hashlib.sha256((directory / archive).read_bytes()).hexdigest() != receipt["sha256"]:
        raise ValueError("release archive does not match its receipt")
    expected = {archive, "vpn-pin.rb", "RELEASE.json"}
    seen = set()
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if name not in expected or name in seen or hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("release asset checksum mismatch")
        seen.add(name)
    if seen != expected:
        raise ValueError("release checksums are incomplete")
    tag = "v" + version
    current = tag_commit(repo, tag)
    if current is None:
        subprocess.check_call(["gh", "api", "--method", "POST", "repos/" + repo + "/git/refs",
                               "-f", "ref=refs/tags/" + tag, "-f", "sha=" + commit], stdout=subprocess.DEVNULL)
    elif current != commit:
        raise ValueError("existing version tag points at a different commit; use a new version")
    if tag_commit(repo, tag) != commit:
        raise ValueError("remote tag changed before draft creation; stopping")
    notes = Path(__file__).resolve().parents[1] / "docs/RELEASE_NOTES.md"
    subprocess.check_call(["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--draft",
                           "--target", commit, "--title", "vpn-pin " + version, "--notes-file", str(notes),
                           *(str(directory / name) for name in sorted(expected | {"SHA256SUMS"}))])
    if tag_commit(repo, tag) != commit:
        raise ValueError("remote tag changed; draft is not ready for publication")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    create_draft(args.repo, args.directory, args.commit)
