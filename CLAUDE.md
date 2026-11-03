# Project guide

vpn-pin is a macOS CLI for independent OpenVPN tunnels and selected IPv4 host routes. Python 3.9+ standard library only; OpenVPN is external. Kubernetes and aws-vault are optional.

## Files

- `bin/vpn-pin`: shell launcher and Python discovery.
- `lib/vpn_pin.py`: profile parsing, dependency discovery, tunnel lifecycle, root-owned state, route updates, and CLI.
- `examples/profiles.example`: non-executable configuration template.
- `setup.sh`: non-destructive symlink installation.
- `tests/`: mocked regression tests and temporary installer checks.

## Check

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Keep edits scoped. Preserve resolution-before-mutation, explicit route conflicts, rollback, private runtime state, and process identity checks. Never use live routes, sudo, private configurations, or VPN tunnels in tests. Do not copy private endpoint addresses or credentials into fixtures, docs, or reports. See README for current live-validation limits.
