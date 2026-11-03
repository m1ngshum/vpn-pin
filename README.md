# vpn-pin

Run separate OpenVPN tunnels on macOS and route selected IPv4 hosts through them. Keep your upstream connection, including an existing VPN, for other traffic. Profiles can target any hostname or IPv4 address; Kubernetes and aws-vault are optional.

This is an early public candidate. Automated tests use mocked routes and processes; a live tunnel has not yet been validated.

## Requirements

- macOS and Python 3.9 or newer.
- OpenVPN 2.6 or newer as the intended compatibility baseline. Live compatibility with these versions has not yet been verified.
- Your own trusted OpenVPN client configuration and working credentials.
- Optional: `kubectl` for Kubernetes targets; `aws-vault` for the Kubernetes command wrapper.

Python uses only the standard library. The launcher checks Apple Silicon Homebrew, Intel Homebrew, and system Python locations, including when sudo supplies a limited PATH. OpenVPN and optional tools are discovered from PATH or common Homebrew locations.

## Install

From this checkout:

```sh
./setup.sh
export PATH="$HOME/.local/bin:$PATH"
vpn-pin --help
```

The installer creates a symlink at `~/.local/bin/vpn-pin`; keep the checkout in place. It refuses to replace an existing file. Use `PREFIX=/another/location ./setup.sh` to choose a different prefix. It does not install dependencies, change shell configuration, or start a VPN.

## Configure

Keep configurations and credentials outside the checkout:

```sh
mkdir -p "$HOME/.config/vpn-pin"
chmod 700 "$HOME/.config/vpn-pin"
cp examples/profiles.example "$HOME/.config/vpn-pin/profiles.conf"
chmod 600 "$HOME/.config/vpn-pin/profiles.conf"
```

Edit that file before running a tunnel. Each non-comment line has five pipe-separated fields:

```text
name|absolute .ovpn path|comma-separated targets|optional Kubernetes context|optional aws-vault profile
work|~/vpn/work.ovpn|api.example.test,192.0.2.10||
cluster|~/vpn/cluster.ovpn||my-context|my-cloud-profile
```

The hostnames and addresses above are documentation examples; replace them with your own. Supply targets, a Kubernetes context, or both. Hostnames resolve to IPv4 addresses; a context adds the host from its configured API endpoint. The aws-vault field applies only to `kube` commands.

Names contain letters, digits, underscores, or hyphens and start with a letter or digit. Paths support spaces and a leading `~/`; quotes, backslashes, and pipe characters are not supported. The file is parsed as data and never sourced as shell code. Use `--config /path/to/profiles.conf` before the command to select another file.

## Use

```sh
vpn-pin profiles
vpn-pin up work
vpn-pin status
vpn-pin refresh work
vpn-pin down work
vpn-pin reconnect work
vpn-pin doctor
vpn-pin kube cluster -- get namespaces
```

Tunnel commands and status request sudo when needed. Kubernetes commands run as your user, inherit the terminal, and preserve the command's exit status. `down all` and `reconnect all` operate on all configured profiles. Stop a profile before removing or renaming it in the configuration.

`refresh` resolves replacement addresses before changing routes. It refuses conflicting exact host routes, records mutation intent, adds new routes first, and attempts rollback if a change fails or is interrupted. Failed rollback records the routes that remain. `status` checks the recorded process identity and marks routes whose interface is missing or changed; it does not prove endpoint reachability.

## Routing and recovery

vpn-pin disables OpenVPN's automatic route execution, including routes in your local client configuration. Only the selected IPv4 host routes are pinned. It does not configure DNS, monitor changing DNS records, retry refreshes automatically, alter kill switches, or add IPv6 routes. `doctor` shows the local IPv4 routing table for inspection.

Only use trusted OpenVPN configurations: they run with elevated privileges and can enable scripts or plugins that alter the system. Existing VPN kill switches or local-network blocking can prevent another tunnel from working; vpn-pin does not bypass those settings.

Runtime state, generated wrappers, and OpenVPN logs live under the root-owned `/var/run/vpn-pin/<uid>` directory. Process identity is checked using the recorded command path, start time, PID, and wrapper path before signaling, including after OpenVPN drops privileges. The command path comes from process arguments rather than independent executable-image verification. If a stale process record still has routes, cleanup stops for manual inspection rather than deleting potentially unrelated routes. An unfinished route journal also stops automatic operations: its `pending_routes` are possible affected routes, not proof of ownership. Inspect the reported state file and routing table before manual recovery; do not clear intent or remove a route until its ownership is established.

State is lost on reboot. Startup interface detection relies on OpenVPN's standard English log message. A failed startup leaves diagnostic logs; `down` can recover a recorded unfinished startup. Reconnect stops the tunnel before starting it, so startup failure leaves that profile down.

## Development

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests use temporary files and mocks without starting tunnels, invoking sudo, reading private VPN configurations, or changing live routes. See [CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under [MIT](LICENSE).

For private vulnerability reports, see [SECURITY.md](SECURITY.md).
