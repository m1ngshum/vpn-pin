# Stable 0.2.0 qualification

Support status: 0.2.0 is scoped to awake, operator-managed sessions. The live-validation baseline was `v0.2.0-rc.5`, source `417760bb8a8b85d732b3158522eac4b9f48ed839`. Runtime changes for 0.2.0 add support notices only; routing, process ownership, PF journals and cleanup logic are unchanged. Exact-source CI, review and artifact/install verification are recorded in the preparation PR and release receipts; live evidence and publication verification remain distinct.

## Adopted support scope (2026-11-09)

The maintainer adopted awake, operator-managed sessions on the validated configuration as the 0.2.0 stable support contract. Supported evidence covers selected host routing, DNS refresh, eligible awake network-interruption recovery without active PF grants, and explicitly supervised API PF access/cleanup. Running any tunnel or PF exception across sleep, including sleep/wake recovery, is unsupported. Unattended and combined-fault recovery remain outside support.

Operators must stop clients/watchers and complete managed down with verified cleanup before sleep; after waking from a clean shutdown they establish fresh sessions and verify TLS/authentication. Unexpected sleep or ambiguous cleanup requires inspection, not unconditional reconnect or journal deletion. PF supervision cannot execute while asleep, and permissions/journals may remain.

Sleep qualification is not a gate for this explicitly narrower support scope. The failed attempt below remains a failed attempt; this support decision neither turns it into a pass nor extends validation beyond the recorded environment. Further sleep tests remain stopped unless separately authorized.

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

## Physical interruption and unclean-exit qualification (2026-11-08)

A corrected bounded trial against unchanged installed rc.5 passed 16 functional stages on the same live configuration. Wi-Fi was physically disabled for 90.6 seconds with a detached, serialized local restoration watchdog; no sleep, power-setting, root-PF reload, or default-route repair was performed. No API-PF grant was active during the outage. Each of three automatic watchers created exactly one fresh, verified session, recorded by the private startup observer; no replacement interface was adopted.

| Check | Scope and result |
| --- | --- |
| Physical upstream loss and fresh automatic recovery | Passed on three profiles; Wi-Fi and original default route restored |
| PF supervisor SIGKILL with real connection states | Passed on three profiles; exact permissions/journal remained until explicit cleanup |
| Unfinished PF journal refuses refresh/reconnect/automatic watch | Passed on three profiles after supervisor death |
| Explicit down after PF supervisor death | Passed on three profiles; owned rules/states revoked before tunnel teardown |
| Watcher SIGKILL | Passed on three profiles; healthy owned session remained usable by refresh |
| OpenVPN SIGKILL without active PF | Passed on three profiles; kernel removed routes, explicit down cleared stale state, then fresh startup succeeded |
| Authenticated identity, bounded namespace read, certificate-verified TLS | Passed on all three profiles after physical recovery, after PF crash recovery, and after watcher/OpenVPN crash recovery |

All nine authenticated profile-rounds passed; each round also verified TLS at six API addresses. API clients closed, all owned tunnels/routes/helpers exited, and an independent cleanup retry passed. Original default routing, private profile bytes/mode, resolver configuration, OpenVPN process baseline, root PF rules and PF enabled status were preserved.

The raw recursive PF inventory differs by nine new, empty, exactly run-owned anchor containers. The trial conservatively retained `restored=false` for that inventory difference. A separate read-only verifier removed only those exact empty blocks from the captured comparison and established byte-for-byte equality with the original recursive rules; it did not mutate PF. Empty containers carry no permissions. Prior closed-journal transitions were checked during execution; saved permission snapshots are pre-cleanup records, not independent retained copies of every closed journal.

An earlier attempt stopped at a test-observer race while fresh startup state preceded its receipt. Its owned resources were cleaned, but it detected a root PF printed-output hash mismatch before any API-PF grant. The observer was fixed to publish receipts atomically and inspect them under the startup lock, with 12 fabricated safety/race checks and Astra review before the corrected trial. The corrected trial preserved the complete foreign policy. No product-code fix was required.

Read-only investigation of the earlier mismatch found the same starting hash in four prior successful runs. However, the first attempt retained only that starting hash and a failed equality check: neither the before/after rule text nor the final hash was saved. A later baseline cannot stand in for that missing final snapshot. The difference therefore cannot be classified as formatting, ordering, changed predicates, or a security-policy change. Available time-bounded system/primary-VPN logs identify neither a PF writer nor an active upstream-VPN reconnect. The reached vpn-pin paths contain no root-policy write, and no API-PF grant ran; trusted external OpenVPN hooks or another process were not ruled out.

The later harness fixes this evidence gap prospectively by capturing ordered root and recursive rules plus PF enabled status at baseline, after physical restoration, before PF grants, and after cleanup. Runtime hash, ownership and cleanup safeguards remain unchanged. The historical mismatch and its cause remain unresolved; the successful corrected trial does not erase that observation. On 2026-11-08 the maintainer accepted this documented historical evidence gap for stable preparation; this acceptance does not cover new policy differences.

The crash checks qualify explicit operator recovery in these separate fault scenarios, not unattended crash recovery or combined PF-supervisor/tunnel ownership loss. No sleep/wake request was made during that trial.

## Final-runtime sleep attempt (2026-11-09)

Pre-sleep authenticated identity, bounded namespace reads and certificate-verified TLS passed on all three profiles against installed rc.5. Those PF grants were closed before the sleep request, and three automatic recovery watchers were attached. A uniquely owned one-shot wake event and independent restoration guard supervised temporary sleep enablement; other power settings were not changed.

The actual system log showed four seconds of software-requested sleep, a 45-second DarkWake, then 35 seconds of maintenance sleep before full wake. Neither sleep interval met the required 75 uninterrupted seconds. The trial rejected the cycle, performed no post-wake authentication round, and does not qualify sleep recovery. The maintainer stopped further sleep tests; a reviewed longer retry was not executed.

The original sleep-disable setting was restored, the owned wake event was absent, and the guard exited successfully. API clients, watchers, owned PF rules/states, tunnels and routes were cleaned; an independent cleanup retry passed. Default routing, private profiles, resolver configuration and process baseline were preserved. Foreign PF rules matched after excluding only three new empty owned anchor containers. Unrelated scheduled wake entries changed, so the raw run retained `restored=false`; those entries were not repaired or cancelled. No product-code defect was established.

A separate non-sleep follow-up then established fresh sessions on all three profiles and passed authenticated identity, bounded namespace reads and certificate-verified TLS at six API addresses. Owned permissions/states, tunnels, routes and helpers were removed; baseline checks and an independent cleanup retry passed. No sleep request, wake scheduling or power-setting change ran in that follow-up. It verifies fresh-session access after the observed wake, not sleep continuity or successful sleep recovery.

## Candidate checks and publication gates

The awake-only candidate passed 125 mocked tests on Python 3.9.6 and 3.14.10, plus shell syntax and diff checks. Four added tests cover configuration/privilege-free help and stderr notices before tunnel startup, watcher refresh and PF grants. No new live VPN or power test was run for this notice/version change. Record the final exact-commit CI, review and local artifact/isolated-installation results in the preparation PR before any merge.

- Review the awake-only documentation, agent skills, CLI disclosures and candidate changes; require exact-commit checks and Astra review.
- Build and verify the committed 0.2.0 candidate archive, formula, checksums and isolated installation. Existing rc.5 live evidence applies to unchanged core behavior, not a new live validation claim for this candidate.
- After separately authorized merge, require green CI and exact-source immutable artifact verification before separately authorized publication and verified Homebrew installation/upgrade. No tag, release draft, publication or global installation is authorized by candidate preparation.

## Security limits

PF supervision is periodic. Unclean exits/SIGKILL may require explicit `down` or operator inspection; the bounded crash checks above do not establish unattended or combined-fault recovery. PF-managed sessions freeze targets and block automatic reconnect. Lost interface/process ownership or uncertain cleanup retains journals and refuses unsafe mutation. Trusted OpenVPN configurations, including scripts/plugins and referenced files, remain the operator's responsibility.

Private evidence is retained outside the repository. Public records contain sanitized outcomes, not credentials, client files, infrastructure addresses, or resource identities.
