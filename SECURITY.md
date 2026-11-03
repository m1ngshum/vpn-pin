# Security

This project is an early candidate. Live macOS/OpenVPN compatibility has not yet been validated. Security fixes target the current default branch.

Report vulnerabilities privately using [GitHub private vulnerability reporting](https://github.com/m1ngshum/vpn-pin/security/advisories/new). Do not put credentials, private client configurations, certificates, infrastructure addresses, personal paths, or unredacted diagnostics in public issues.

Only use trusted OpenVPN configurations. They run with elevated privileges and may enable scripts or plugins. Keep configurations and credentials outside the checkout. Automated tests must never start tunnels or alter live routes.
