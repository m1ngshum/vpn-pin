# vpn-pin 0.2.0-rc.3

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
