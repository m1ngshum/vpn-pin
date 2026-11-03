# Project guide

vpn-pin is a macOS CLI for independent OpenVPN tunnels and selected IPv4 host routes. Python 3.9+ standard library only; OpenVPN is external. Kubernetes and aws-vault are optional.

## Files

- `bin/vpn-pin`: shell launcher and Python discovery.
- `lib/vpn_pin.py`: profile parsing, dependency discovery, tunnel lifecycle, root-owned state, route updates, and CLI.
- `examples/profiles.example`: non-executable configuration template.
- `setup.sh`: non-destructive symlink installation.
- `scripts/build_release.py`: deterministic committed-file archive, checksums, and Homebrew formula.
- `docs/RELEASING.md`: draft release, manual publication, and Homebrew update procedure.
- `tests/`: mocked regression tests and temporary installer checks.

## Check

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Keep edits scoped. Preserve resolution-before-mutation, explicit route conflicts, rollback, private runtime state, and process identity checks. Never use live routes, sudo, private configurations, or VPN tunnels in tests. Do not copy private endpoint addresses or credentials into fixtures, docs, or reports. See README for current live-validation limits.

Plan and refresh share one route planner. Watch must release its lock before waiting or looking up DNS, re-read ownership every tick, preserve routes on lookup failure, and stop on ambiguity. Profile edits preserve existing content and permissions, reject unsafe files, and detect concurrent changes. Release assets must come from an exact commit, never uncommitted files; draft creation does not authorize publication.
