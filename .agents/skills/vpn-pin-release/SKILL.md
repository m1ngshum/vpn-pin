---
name: vpn-pin-release
description: Prepare, review, publish, and verify vpn-pin releases and Homebrew formula updates with exact source and artifact identity.
---

# Release vpn-pin

Use the repository [release guide](../../../docs/RELEASING.md) and scripts as the source of truth. Distinguish preparation, merge, tag creation, release publication, and tap publication. Perform the stages authorized in the current conversation; do not request the same authorization again or treat a preparation request as approval to publish.

Before merging, check the exact PR head, required checks, review findings, and working-tree state. Preserve unrelated edits. Merge with the expected head SHA; do not bypass branch protection. Before release, confirm the merged source commit and CI, then build that exact commit with `scripts/build_release.py`. The builder ignores uncommitted files and emits the install archive, formula, commit receipt, and checksums.

Use a prerelease version such as `0.2.0-rc.1` while live compatibility is unverified; do not consume a stable tag accidentally. Enable release immutability before publication. The main-only **Draft release** workflow uses `scripts/create_draft.py` to reject mismatched assets or version tags. Inspect the resulting draft and assets against the chosen commit; a successful build alone is not publication proof. Never move a published tag or replace immutable assets: release a new version.

When publication is authorized, publish the reviewed draft with prerelease status as appropriate, download the published assets, verify their checksums and release attestation, and check the remote tag against `RELEASE.json`. If a tag or asset disagrees, stop rather than silently substituting another commit.

For an authorized tap update, take `vpn-pin.rb` from the verified release and update `Formula/vpn-pin.rb` in the Homebrew tap through review and CI. Verify actual installation and `brew test`; do not start tunnels during packaging tests or upgrade unrelated installed VPN dependencies. Keep profiles/runtime state outside the install prefix. Report release URL, version, reviewed commit, formula checksum, CI/installation evidence, and remaining live-validation limits.
