# Stable 0.2.0 qualification

Status: preparation only; not approved for stable publication. Published/runtime baseline: `v0.2.0-rc.5`, source `417760bb8a8b85d732b3158522eac4b9f48ed839`.

## Completed baseline

- 121 mocked regression tests passed on Python 3.9 and 3.14. These are not live network tests.
- Installed rc.5 was independently checked against released runtime bytes.
- Bounded live checks on 2026-11-08 passed on three profiles: authenticated identity and read-only namespace requests, certificate-verified TLS at six API addresses, owned PF rules/state cleanup, and complete owned tunnel/route/helper cleanup.
- Foreground PF supervisors exited successfully; an independent cleanup retry passed. Default routing, root PF policy, process baseline, and private profile bytes/mode were preserved. Astra reviewed the private receipts with no findings.
- Homebrew release installation and `brew test` passed; release checksums and immutable provenance were verified.

Live configuration: macOS 27.3 on Apple Silicon, Homebrew Python 3.14.10, OpenVPN 2.7.8_1, lz4 1.11.0. Other platforms/versions remain unverified.

## Non-sleep qualification in progress

The planned bounded checks use fresh profile sessions and a separate private profile file. Original client configuration contents and credentials are not copied into public artifacts.

| Check | Status |
| --- | --- |
| Actual scoped DNS answer change and watcher refresh | Pending |
| Actual NXDOMAIN during refresh/watch preserves owned routes | Pending |
| Existing foreign host-route conflict remains untouched | Pending |
| Ctrl-C stops watch while retaining the healthy owned tunnel | Pending |
| Default watch refuses a controlled profile lifecycle replacement | Pending |
| One fresh owned automatic restart after eligible profile lifecycle loss | Pending |
| PF foreground Ctrl-C/SIGTERM revoke rules/states and retain healthy tunnel | Pending |
| Active PF mode refuses refresh, reconnect, and automatic watch | Pending |
| Authenticated API access after controlled recovery on three profiles | Pending |
| Fixture, PF, route, process, and profile baseline restoration | Pending |

A controlled OpenVPN SIGHUP is a profile lifecycle-reset test. It is not evidence of physical network interruption or sleep/wake recovery. Automatic restart is eligible only with the original positively owned process, intact logging, unchanged configuration, and conclusively absent target routes. Guard refusal must not be turned into success by manually pruning routes or adopting another session.

## Remaining stable gates

- Final-runtime sleep/wake and physical upstream-network recovery qualification on a disposable macOS test system, including authenticated access and complete cleanup. No such test is authorized by the current non-sleep task.
- Review any fault-test findings and the final stable support/security documentation.
- After qualification: final `0.2.0` version/notes commit, exact-commit review and green CI, then separately authorized immutable publication and verified Homebrew installation/upgrade.

## Security limits

PF supervision is periodic. Unclean exits/SIGKILL may require explicit `down` or operator inspection; the current live tests do not qualify crash recovery. PF-managed sessions freeze targets and block automatic reconnect. Lost interface/process ownership or uncertain cleanup retains journals and refuses unsafe mutation. Trusted OpenVPN configurations, including scripts/plugins and referenced files, remain the operator's responsibility.

Private evidence is retained outside the repository. Public records contain sanitized outcomes, not credentials, client files, infrastructure addresses, or resource identities.
