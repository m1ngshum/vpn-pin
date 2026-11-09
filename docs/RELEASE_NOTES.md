# vpn-pin 0.2.0

- Adopt an awake-only stable support contract for 0.2.0, based on the validated rc.5 runtime with added support notices and release metadata.
- Running any active tunnel or PF exception across sleep, including sleep/wake recovery, is unsupported. Stop clients/watchers, complete managed down and verify cleanup before sleep; use fresh sessions and verify TLS/authentication after waking from a clean shutdown. Unexpected sleep or ambiguous journals requires inspection, not forced reconnect.
- Add awake-only notices to CLI help and tunnel, watch and PF startup on stderr. Shared tunnel startup also covers reconnect and automatic restart. Ownership checks, routing/PF mutation and cleanup behavior remain unchanged. Sleep qualification is excluded by the adopted support scope, while the failed attempt remains recorded.
- Correct the stale security statement that no live validation had been performed. Document the bounded authenticated API/PF-cleanup evidence and the limits of periodic foreground supervision.
- Scoped non-sleep qualification passed on installed rc.5: actual private-domain DNS rotation and NXDOMAIN handling, foreign-route conflict refusal, watcher interruption, conservative lifecycle refusal, and exactly one fresh automatic restart on each of three profiles. PF interruption cleanup and mutation guards passed; all three profiles passed authenticated API reads and certificate-verified TLS after recovery, followed by complete fixture/session cleanup and preserved baselines.
- A corrected physical-network/crash trial passed 16 functional stages: a 90.6-second Wi-Fi outage with fresh automatic recovery on three profiles, separate PF-supervisor/watcher/OpenVPN SIGKILL checks, explicit safe cleanup, and three successful authenticated API/TLS rounds on every profile. Effective foreign PF policy was preserved; nine empty owned anchors remain. An earlier attempt's root-PF output hash mismatch remains disclosed: read-only investigation found no confirmed product defect, but missing historical snapshots prevent identifying the difference or writer. The maintainer accepted that documented historical evidence gap; new policy differences remain subject to inspection. Later evidence capture closes the diagnostic gap prospectively without weakening runtime checks. See [validation status](VALIDATION.md) for evidence, observer corrections and remaining gates. Final-runtime sleep/wake and unattended or combined-fault crash recovery remain unverified.
- A final-runtime sleep attempt was rejected: DarkWake split the cycle into four- and 35-second sleep intervals, below the qualification threshold. Pre-sleep authenticated access passed on three profiles; owned resources and the temporary power change were cleaned, while unrelated wake-schedule changes remained recorded as a restoration difference. Further sleep tests were stopped by the maintainer. A separate non-sleep follow-up passed fresh-session authenticated access/TLS on all three profiles with verified cleanup. This is not a sleep-recovery pass.

Release verification binds the source commit, immutable archive, formula, receipt and checksums. Installation and upgrades must preserve private profiles/runtime state and occur only after active sessions have been stopped. The release does not claim sleep/wake, unattended or combined-fault recovery.

# vpn-pin 0.2.0-rc.5

- Add opt-in `watch --auto-reconnect` for one bounded restart after verified tunnel continuity loss. Recovery requires the original positively owned process, intact lifecycle logging, unchanged configuration, and conclusively absent target routes. It confirms the old PID has exited and accepts only the fresh session it creates; default watch remains conservative.
- Add explicit foreground `api-pf PROFILE` for API-only IPv4 TCP 443 PF exceptions on a verified tunnel interface/source and frozen target addresses. It requires an existing enabled PF policy and anchor hook; it never reloads root PF rules or enables/disables PF. Unfinished PF journals block route/lifecycle changes, including automatic reconnect.
- Bind permissions to a root-private journal and an independent runtime reference. Revoke exact owned rules and scoped connection states before tunnel teardown. Changed ownership, foreign rules, and uncertain cleanup retain recovery evidence and stop teardown rather than adopting a replacement interface.
- Document command-scoped macOS credential prompts with `AWS_VAULT_PROMPT=osascript` for Kubernetes commands using aws-vault.
- All 121 mocked regression tests passed on Python 3.9 and 3.14. Astra reviewed the implementation and completed live-test evidence with no remaining findings.
- Bounded live checks on the released runtime passed for three profiles: certificate-verified TLS at six API addresses, authenticated identity, and read-only namespace requests. All foreground supervisors exited successfully; API clients, owned permissions/states, tunnels, and routes were cleaned. Default routing, root PF policy, process baseline, and private profile bytes/mode were preserved; an independent cleanup retry also passed.

This remains a prerelease. No additional sleep/wake cycle was run on this final runtime; earlier recovery-candidate sleep results do not establish final-release sleep compatibility. PF supervision is periodic, not a kernel binding to process identity: SIGKILL or an unclean supervisor exit may leave permissions/journals until explicit `down` or operator inspection. PF targets stay frozen, and PF-managed profiles do not automatically reconnect. Bounded checks do not establish compatibility with every macOS or VPN configuration.

# Previous: 0.2.0-rc.4

- Make Homebrew credential helpers available to Kubernetes commands under a restricted PATH, preserving existing PATH precedence and applying the environment after privilege drop.
- Allow explicit cleanup of a positively identified tunnel process when recorded host routes are conclusively absent. Existing routes still require verified tunnel continuity and ownership; failed or malformed lookups preserve state and stop cleanup.
- Added regressions for credential-helper precedence, delegated environments, uncertain route lookups, absent routes, and foreign-route protection. All 63 tests passed on Python 3.9 and 3.14.
- Bounded live checks passed for three existing profiles: TCP/TLS, authenticated identity, a read-only namespace request, restarted watchers, and cleanup. A real sleep/wake cycle changed interface lifecycles, causing watchers to stop conservatively. After unlock, explicit cleanup and fresh reconnection restored authenticated access on all three profiles. Original routing, private profiles, and power settings were preserved.

This remains a prerelease. Interface recreation requires inspection and explicit reconnect; automatic adoption of a replacement interface is not supported. Bounded checks do not establish compatibility with every macOS or VPN configuration.

## Previous: 0.2.0-rc.3

- Ignore macOS cloned fallback host-cache routes when checking for explicit route conflicts. Deliberate host routes remain protected.
- Added regression coverage for cloned fallback and scoped static host routes.
- Bounded live checks passed for three existing OpenVPN profiles: startup, route planning, diagnostics, refresh, certificate-verified TLS, watch interruption, and cleanup. The upstream default route and starting process state were restored. Unauthenticated requests returned HTTP 401; authenticated API access and sleep/wake recovery were not tested.

This remains a prerelease. Automated tests use mocks; the bounded live checks do not establish compatibility with every macOS or VPN configuration.

## Previous: 0.2.0-rc.2

- Added read-only route previews with the same planning logic used by refresh.
- Added dependency, DNS, route, and process diagnostics; optional bounded TCP checks.
- Added foreground watch mode with DNS retry backoff, per-tick ownership checks, and no automatic tunnel startup/shutdown.
- Added atomic profile creation and metadata/DNS validation without executing the profiles file.
- Added version reporting, reproducible install archives, checksums, a generated Homebrew formula, and a manual draft-release workflow.
- Added repository AI skills for scoped operation/diagnostics and verified release/Homebrew workflows.
- Pinned gzip compression so archive checksums do not depend on Python's default compression setting. The unpublished rc.1 draft was superseded without moving its tag.

This is an early prerelease. Automated validation uses mocks and temporary installation directories. Live macOS/OpenVPN operation remains unverified. Packaging and published installation verification are reported separately from tunnel compatibility.
