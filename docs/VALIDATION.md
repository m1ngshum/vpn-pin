# Stable 0.2.0 qualification

Status: preparation only; not approved for stable publication. Published/runtime baseline: `v0.2.0-rc.5`, source `417760bb8a8b85d732b3158522eac4b9f48ed839`.

## Completed baseline

- 121 mocked regression tests passed on Python 3.9 and 3.14. These are not live network tests.
- Installed rc.5 was independently checked against released runtime bytes.
- Bounded live checks on 2026-11-08 passed on three profiles: authenticated identity and read-only namespace requests, certificate-verified TLS at six API addresses, owned PF rules/state cleanup, and complete owned tunnel/route/helper cleanup.
- Foreground PF supervisors exited successfully; an independent cleanup retry passed. Default routing, root PF policy, process baseline, and private profile bytes/mode were preserved. Astra reviewed the private receipts with no findings.
- Homebrew release installation and `brew test` passed; release checksums and immutable provenance were verified.

Live configuration: macOS 27.3 on Apple Silicon, Homebrew Python 3.14.10, OpenVPN 2.7.8_1, lz4 1.11.0. Other platforms/versions remain unverified.

## Completed scoped non-sleep qualification (2026-11-08)

The bounded checks used fresh profile sessions and a separate private profile file against installed rc.5, with unchanged installed product files and no simulated route operations. A private observer recorded only the exact session returned by the installed recovery startup function so cleanup could remain bound to that session. Original client configuration contents and credentials were not copied into public artifacts.

| Check | Status |
| --- | --- |
| Actual scoped DNS answer change and watcher refresh | Passed |
| Actual NXDOMAIN during refresh/watch preserves owned routes | Passed |
| Existing foreign host-route conflict remains untouched | Passed |
| Ctrl-C stops watch while retaining the healthy owned tunnel | Passed |
| Default watch refuses a controlled profile lifecycle replacement | Passed |
| One fresh owned automatic restart after eligible profile lifecycle loss | Passed |
| PF foreground Ctrl-C/SIGTERM revoke rules/states and retain healthy tunnel | Passed |
| Active PF mode refuses refresh, reconnect, and automatic watch | Passed |
| Authenticated API access after controlled recovery on three profiles | Passed |
| Fixture, PF, route, process, and profile baseline restoration | Passed |

The DNS fixture used an exclusively owned resolver entry for a unique test-only domain and a loopback responder. Actual product lookups and live watcher-owned host routes switched between two documentation addresses; an actual NXDOMAIN response preserved the existing pins. The entry was removed, and the original resolver-directory manifest was restored. Default resolver settings were not changed and no global cache flush was performed. The conflict check used the preexisting loopback host route and verified that it remained unchanged.

DNS/conflict/default-watch and PF Ctrl-C/SIGTERM checks exercised one profile. Fresh automatic recovery, PF mutation refusals, authenticated identity and namespace reads, certificate-verified TLS, and final cleanup exercised all three profiles. Each automatic watcher created exactly one fresh owned session; Ctrl-C then stopped watching while leaving that healthy session intact. All six API addresses passed certificate verification after recovery.

All final PF supervisors exited `0`; interruption checks exited `130` as expected, and default watch exited on continuity loss. API clients closed, owned PF rules/states and tunnels/routes were removed, earlier closed test anchors remained empty, and an independent cleanup retry passed. Default routing, root PF policy, original private profile bytes/mode, and the OpenVPN process baseline were preserved. A separate final process scan found no test helpers or VPN processes remaining. No product-code fix was needed during this run.

A controlled OpenVPN SIGHUP is a profile lifecycle-reset test. It is not evidence of physical network interruption or sleep/wake recovery. Automatic restart is eligible only with the original positively owned process, intact logging, unchanged configuration, and conclusively absent target routes. Guard refusal must not be turned into success by manually pruning routes or adopting another session.

## Remaining stable gates

- Final-runtime sleep/wake and physical upstream-network recovery qualification on a disposable macOS test system, including authenticated access and complete cleanup. No such test is authorized by the current non-sleep task.
- Review any fault-test findings and the final stable support/security documentation.
- After qualification: final `0.2.0` version/notes commit, exact-commit review and green CI, then separately authorized immutable publication and verified Homebrew installation/upgrade.

## Security limits

PF supervision is periodic. Unclean exits/SIGKILL may require explicit `down` or operator inspection; the current live tests do not qualify crash recovery. PF-managed sessions freeze targets and block automatic reconnect. Lost interface/process ownership or uncertain cleanup retains journals and refuses unsafe mutation. Trusted OpenVPN configurations, including scripts/plugins and referenced files, remain the operator's responsibility.

Private evidence is retained outside the repository. Public records contain sanitized outcomes, not credentials, client files, infrastructure addresses, or resource identities.
