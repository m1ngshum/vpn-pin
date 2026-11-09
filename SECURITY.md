# Security

Version 0.2.0 supports awake, operator-managed sessions on the validated configuration. Bounded live checks of the rc.5 runtime validated selected routing, awake upstream-loss recovery without active PF grants, authenticated API access, and normal and explicit post-crash API-only PF cleanup on three profiles. The release adds notices and release metadata without changing that ownership/cleanup logic. Running tunnels or PF exceptions across sleep, including sleep/wake recovery, is unsupported. General platform, unattended crash-recovery and combined-fault compatibility are not established. See [validation status](docs/VALIDATION.md), including the unresolved historical PF output hash mismatch and its evidence limits. Security fixes target the current default branch.

Report vulnerabilities privately using [GitHub private vulnerability reporting](https://github.com/m1ngshum/vpn-pin/security/advisories/new). Do not put credentials, private client configurations, certificates, infrastructure addresses, personal paths, or unredacted diagnostics in public issues.

Only use trusted OpenVPN configurations. They run with elevated privileges and may enable scripts or plugins. Keep configurations and credentials outside the checkout. Automated tests must never start tunnels or alter live routes.

Before sleep, stop clients and watchers, complete managed `down` for every active profile and verify cleanup while ownership is still valid. After waking from that clean shutdown, establish fresh sessions and verify TLS and authenticated access. Unexpected sleep or failed down requires inspection, not forced reconnect. The supervisor cannot execute while asleep; PF permissions may remain, and subsequent ownership loss can prevent safe state cleanup. vpn-pin does not prevent sleep.

API-only PF permissions are an explicit foreground opt-in. Supervision periodically checks tunnel ownership; it is not a kernel binding to process identity. SIGKILL, a supervisor crash, or an unclean exit can leave permissions and recovery journals until explicit `down` or operator inspection. Changed ownership or uncertain state cleanup stops teardown and retains evidence. Never delete a journal to force recovery or flush unrelated PF rules/states.

Only IPv4 TCP 443 to frozen profile targets is supported in this mode. An unfinished PF journal blocks route refresh and automatic reconnect. Reconnect requires a new explicitly verified session after cleanup. Client configurations are trusted elevated code; scripts, plugins, and referenced files remain the operator's responsibility.
