# vpn-pin 0.2.0

- Added read-only route previews with the same planning logic used by refresh.
- Added dependency, DNS, route, and process diagnostics; optional bounded TCP checks.
- Added foreground watch mode with DNS retry backoff, per-tick ownership checks, and no automatic tunnel startup/shutdown.
- Added atomic profile creation and metadata/DNS validation without executing the profiles file.
- Added version reporting, reproducible install archives, checksums, a generated Homebrew formula, and a manual draft-release workflow.

Automated validation uses mocks and temporary installation directories. Live macOS/OpenVPN operation and installation from published Homebrew assets remain unverified. Mark this as a prerelease until those checks are completed.
