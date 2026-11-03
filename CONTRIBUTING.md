# Contributing

Keep changes small and include a regression test for routing, process ownership, or configuration changes. Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests must use mocks and temporary files. Do not start tunnels, invoke sudo, change routes, or read private VPN configurations during automated tests.

Do not commit client configurations, certificates, keys, credentials, runtime logs, real infrastructure addresses, or personal paths. Use documentation addresses and `.test` hostnames in examples. Redact issue attachments as well as command output.

Describe the behavior changed, how it was tested, and whether live macOS/OpenVPN behavior was checked. A passing mocked test suite does not establish live compatibility. Contributions are provided under the project's MIT license.
