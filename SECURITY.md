# Security

The published version is a prerelease; stable 0.2.0 is in preparation. Bounded live macOS/OpenVPN checks have validated selected routing, physical upstream-loss recovery, authenticated API access, and normal and explicit post-crash API-only PF cleanup on three profiles. This does not establish general platform, sleep/wake, unattended crash-recovery, or combined-fault compatibility. See [validation status](docs/VALIDATION.md), including the unresolved historical PF output hash mismatch and its evidence limits. Security fixes target the current default branch.

Report vulnerabilities privately using [GitHub private vulnerability reporting](https://github.com/m1ngshum/vpn-pin/security/advisories/new). Do not put credentials, private client configurations, certificates, infrastructure addresses, personal paths, or unredacted diagnostics in public issues.

Only use trusted OpenVPN configurations. They run with elevated privileges and may enable scripts or plugins. Keep configurations and credentials outside the checkout. Automated tests must never start tunnels or alter live routes.

API-only PF permissions are an explicit foreground opt-in. Supervision periodically checks tunnel ownership; it is not a kernel binding to process identity. SIGKILL, a supervisor crash, or an unclean exit can leave permissions and recovery journals until explicit `down` or operator inspection. Changed ownership or uncertain state cleanup stops teardown and retains evidence. Never delete a journal to force recovery or flush unrelated PF rules/states.

Only IPv4 TCP 443 to frozen profile targets is supported in this mode. An unfinished PF journal blocks route refresh and automatic reconnect. Reconnect requires a new explicitly verified session after cleanup. Client configurations are trusted elevated code; scripts, plugins, and referenced files remain the operator's responsibility.
