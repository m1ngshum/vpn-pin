# Releases and upgrades

The build script and merging changes do not publish a release or Homebrew tap. Each publication stage below is explicit.

Maintainers should run these commands from a complete Git checkout, rather than the smaller installation archive.

## Build and review

1. Update `VERSION` and `docs/RELEASE_NOTES.md` for each release.
2. Commit the changes and run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
3. Obtain review, fix findings, and require green CI on the exact commit to release.
4. Build locally with `python3 scripts/build_release.py --commit HEAD --output dist`.

The builder reads committed files rather than the working tree. Repeating a build of the same commit produces identical archives and checksums. Assets are `vpn-pin-VERSION.tar.gz`, `vpn-pin.rb`, `RELEASE.json` (commit and archive checksum), and `SHA256SUMS`. The archive contains tracked runtime files and documentation only; untracked configs, reports, Git metadata, and build outputs are excluded. The formula's URL and SHA-256 bind it to that exact release asset.

Before a stable release, define and qualify its declared support scope. For 0.2.0 the adopted scope is awake, operator-managed sessions on the validated configuration: selected host routes, unchanged upstream default routing, DNS changes/failures, route conflicts, eligible awake network recovery without active PF grants, foreground PF access/cleanup, interruption handling and complete owned cleanup. Record environment versions and evidence. Mocked CI does not establish live compatibility. Sleep/wake and carrying active tunnels/PF grants across sleep are explicitly unsupported, not mandatory qualification gates for this scope; preserve failed sleep evidence and require renewed authorization before any additional sleep test. If live compatibility for the declared supported scope is unverified, use an explicitly labeled prerelease.

Current candidate preparation is tracked in [validation status](VALIDATION.md). The checkout prepares `VERSION=0.2.0`, while published and installed versions remain rc.5. Review consistent awake-only notices in README, security/release documentation, repository skills and CLI help/startup before approving the candidate. Verify the exact version/notes commit, tests, CI, reproducible assets and isolated installation; a prepared version number does not authorize a tag, release workflow, merge or publication. After separately authorized merge, repeat exact-source build/checks for the chosen main commit and perform only the separately authorized publication stages.

## Create a draft after merge

The **Draft release** workflow is manual and restricted to `main`. It reruns tests, builds the exact workflow commit, and creates a draft with all four assets attached. Its job has permission to write release assets; the GitHub CLI token is supplied to draft creation and checkout does not persist credentials. The helper checks artifact checksums and receipt, creates the version tag at that exact commit if absent, and rejects an existing tag pointing elsewhere. It verifies the remote tag before and after draft creation. It never publishes the draft automatically.

```sh
gh workflow run release.yml --repo m1ngshum/vpn-pin --ref main
gh run list --repo m1ngshum/vpn-pin --workflow release.yml
gh release view v0.2.0 --repo m1ngshum/vpn-pin
```

Before publishing, enable **release immutability** in the repository's General settings. Review the draft's commit, notes, and assets, and verify that the remote version tag still points to `RELEASE.json`'s commit; select prerelease status when live compatibility remains unverified. Publish manually after approval. Immutable releases lock assets and tags after publication, so attach everything to the draft first. Never move an existing version tag or replace its archive; use a new version.

Download assets and verify checksums with `shasum -a 256 -c SHA256SUMS`. With immutable releases enabled, GitHub CLI can also verify release provenance and assets:

```sh
gh release verify v0.2.0 --repo m1ngshum/vpn-pin
gh release verify-asset v0.2.0 vpn-pin-0.2.0.tar.gz --repo m1ngshum/vpn-pin
```

See [GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) and [verification](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity).

## Publish the Homebrew formula

Distribution uses the dedicated public `m1ngshum/homebrew-tap` repository. Creating the tap is a separate publication action. Download `vpn-pin.rb` from the published release and add it as `Formula/vpn-pin.rb` through a reviewed pull request. Test the actual published archive with `brew install --build-from-source m1ngshum/tap/vpn-pin` and `brew test m1ngshum/tap/vpn-pin` before advertising installation. The formula installs Python and OpenVPN dependencies and uses Homebrew's managed Python interpreter.

After the release and tap exist, users can run:

```sh
brew install m1ngshum/tap/vpn-pin
vpn-pin --version
```

Homebrew may request explicit trust for a third-party formula; trust just this formula when prompted. See [Homebrew taps](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap).

For each update, publish a new version, regenerate its formula from the same commit, and update `Formula/vpn-pin.rb` in the tap through a reviewed pull request. Homebrew then distributes it through normal updates:

```sh
brew update
brew upgrade m1ngshum/tap/vpn-pin
vpn-pin --version
```

Profile data stays in `~/.config/vpn-pin`; runtime state stays in `/var/run/vpn-pin`. Upgrades must not rewrite either. Stop watchers and tunnels explicitly before upgrading; restart them afterward. This avoids changing client versions during an active session.

## Existing checkout installations

The existing symlink installer remains supported and never replaces an unrelated executable. To update a clean checkout on `main`, stop its watchers/tunnels, run `git pull --ff-only`, then check `vpn-pin --version`. For a pinned archive installation, extract each new version separately; the installer refuses to replace the old link. Remove only the symlink you previously created after verifying its target, then run the new archive's setup script. Keep the extracted directory in place.

To migrate to Homebrew, stop the checkout installation's watchers/tunnels, verify and remove only its own `vpn-pin` symlink, then install the formula. No privileged self-updater or shell-piped download script is provided.
