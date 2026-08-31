# Third-party notices

This repository's MIT license applies only to the original build and release automation. ImmortalWrt and every package in the generated firmware retain their own licenses.

The firmware is built from the official ImmortalWrt 25.12.1 `x86/64` ImageBuilder. Every installed package is resolved from the official signed ImmortalWrt feeds; the only local content is the generic bypass-router tuning in `files/`. Each Release includes the exact package manifest, a CycloneDX SBOM, and `build-metadata.json` recording ImageBuilder provenance and resolved package versions.

Primary upstream source: <https://github.com/immortalwrt/immortalwrt>

Notable bundled projects retain their own licenses, including daed (dae), Tailscale, vnStat, open-vm-tools, vlmcsd, and the LuCI frontends packaged alongside them. Consult the per-Release package manifest and SBOM for the authoritative list and versions.
