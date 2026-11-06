---
name: vpn-pin-operate
description: Set up profiles, diagnose connectivity, and operate vpn-pin tunnels on macOS using its route ownership and recovery safeguards.
---

# Operate vpn-pin

Read the repository [README](../../../README.md) for current commands and limitations. Work within the profiles and actions requested by the user; existing conversation authorization remains valid. Do not start/stop tunnels, refresh/watch routes, or edit profiles when the request only asks for diagnosis.

Start with `vpn-pin --version`, `vpn-pin profiles`, and `vpn-pin doctor PROFILE`. Use `vpn-pin plan PROFILE` to preview host-route changes. Plan/status may request sudo to inspect private runtime state. Neither a running process nor a host route proves endpoint reachability. `sudo vpn-pin doctor PROFILE --port PORT` is an explicitly chosen TCP check, not authentication or TLS verification.

Use `profile add` and `validate` for setup; `validate --resolve` additionally checks endpoint lookup. Keep client configs and credentials outside the checkout. These commands validate metadata rather than OpenVPN authentication. Do not read or attach private client configurations, keys, credential files, or full kubeconfigs to diagnose a route problem.

For authorized changes, prefer the CLI over raw route edits or process signals. By default, `watch` refreshes one already-initialized tunnel; it does not start or stop tunnels. Ctrl-C leaves that tunnel running. DNS failures preserve routes; changed process/interface identity, lifecycle discontinuity, conflicts, and unfinished journals require inspection. Do not clear a journal or infer route ownership from a matching utun name/index to force recovery.

An explicitly authorized `watch --auto-reconnect` may restart once after verified continuity loss, using intact log history, the original owned process and unchanged configuration, and conclusively absent routes. It creates and verifies a fresh session rather than adopting an interface. Failed/interrupted recovery or a second loss requires inspection; keep unfinished state and existing ownership checks intact. Default watch remains conservative.

Stop legacy tunnels with the previous version before upgrading. Log continuity checks stop above 4 MiB and assume trusted lifecycle logging. Report what was checked, what changed, and whether TCP or live tunnel behavior was actually verified. Redact private addresses, account names, and paths before posting diagnostics publicly.

`api-pf PROFILE` is an explicit security-policy opt-in, periodically supervised in the foreground. It permits only TCP 443 over a verified owned profile interface/source to frozen targets, using an existing PF hook. Keep it running; Ctrl-C or managed down revokes its own rules and pair-scoped states. Down closes those API connections. An unfinished PF journal blocks route/lifecycle refresh, including automatic reconnect; use down before starting a new PF-managed session. On failed cleanup or lost lifetime, retain the journal and inspect; never flush unrelated rules/states or adopt a replacement interface. SIGKILL/unclean exits are not automatically recovered by a background service.
