# vpn-pin 0.2.0-rc.4

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
