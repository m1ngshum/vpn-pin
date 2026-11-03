#!/usr/bin/env python3
"""Build a reproducible install archive and Homebrew formula from a Git commit."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def build_release(repo, revision, output):
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
    version = git(repo, "show", commit + ":VERSION").decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?", version):
        raise ValueError("VERSION must be a numeric release version, optionally followed by -rc.N")
    paths = ["bin", "lib", "docs", "VERSION", "setup.sh", "README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md"]
    entries = git(repo, "ls-tree", "-rz", commit, "--", *paths).split(b"\0")
    names = set()
    for entry in filter(None, entries):
        metadata, name = entry.split(b"\t", 1)
        if metadata.split()[0] not in (b"100644", b"100755"):
            raise ValueError("release files must be regular files")
        names.add(name.decode())
    if not {"bin/vpn-pin", "lib/vpn_pin.py", "VERSION", "setup.sh", "README.md", "LICENSE"} <= names:
        raise ValueError("release is missing required runtime files")
    archive = "vpn-pin-" + version + ".tar.gz"
    output.mkdir(parents=True, exist_ok=True)
    tar = git(repo, "archive", "--format=tar", "--prefix=vpn-pin-" + version + "/", commit, "--", *paths)
    with (output / archive).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(tar)
    digest = hashlib.sha256((output / archive).read_bytes()).hexdigest()
    formula = '''class VpnPin < Formula
  desc "Selective IPv4 host routing through independent OpenVPN tunnels"
  homepage "https://github.com/m1ngshum/vpn-pin"
  url "https://github.com/m1ngshum/vpn-pin/releases/download/v@VERSION@/vpn-pin-@VERSION@.tar.gz"
  version "@VERSION@"
  sha256 "@DIGEST@"
  license "MIT"

  depends_on :macos
  depends_on "python@3.14"
  depends_on "openvpn"

  def install
    libexec.install "bin", "lib", "VERSION"
    (bin/"vpn-pin").write <<~EOS
      #!/bin/sh
      exec "#{Formula["python@3.14"].opt_bin}/python3.14" "#{libexec}/lib/vpn_pin.py" "$@"
    EOS
    chmod 0755, bin/"vpn-pin"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/vpn-pin --version")
  end
end
'''.replace("@VERSION@", version).replace("@DIGEST@", digest)
    (output / "vpn-pin.rb").write_text(formula)
    (output / "RELEASE.json").write_text(json.dumps({"version": version, "commit": commit, "archive": archive, "sha256": digest}, indent=2) + "\n")
    files = [archive, "vpn-pin.rb", "RELEASE.json"]
    (output / "SHA256SUMS").write_text("".join(hashlib.sha256((output / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in files))
    return commit, version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit, version = build_release(Path(__file__).resolve().parents[1], args.commit, args.output)
    print("Built vpn-pin " + version + " from " + commit)
