# Security Policy

## Supported versions

Only the latest source on `main` and the latest managed Release receive security fixes. Older firmware should be treated as unsupported.

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities, leaked credentials, malicious upstream packages, or exploitable firmware defaults. Use GitHub private vulnerability reporting under the repository's **Security** tab.

Include affected commit or Release tag, reproduction steps, expected impact, and any indicators of compromise. Maintainers will acknowledge a complete report as soon as practical; no fixed response SLA is promised.

## Supply-chain limitations

Some upstreams do not publish independently trusted digests or signing keys. Every Release ships `build-metadata.json` recording the ImageBuilder version, URL and SHA256, the ImmortalWrt commit, the resolved package versions, and the workflow run that produced the artifacts, plus a CycloneDX SBOM and the final package manifest. A successful build does not guarantee that every upstream project is uncompromised.

## Runtime secrets

Never commit router backups, UCI exports, SSH private keys, dashboard passwords, proxy subscription URLs, VPN configuration, or captured traffic. Runtime secrets belong on the deployed VM only, and examples and diagnostics must use redacted placeholders. The setup wizard keeps the daed password, subscription URI, and API token in process memory and writes only non-sensitive values to a state file outside this repository.
