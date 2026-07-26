# Third-party notices

This repository's MIT license applies only to the original automation, configuration, and documentation in this repository. Built firmware contains separately licensed software.

Primary upstream projects include:

| Component | Source | Declared license |
| --- | --- | --- |
| ImmortalWrt | https://github.com/immortalwrt/immortalwrt | GPL-2.0 build environment; packages vary |
| OpenClash | https://github.com/vernesong/OpenClash | MIT |
| OpenWrt Nikki | https://github.com/nikkinikki-org/OpenWrt-nikki | GPL-3.0 |
| PassWall 2 | https://github.com/Openwrt-Passwall/openwrt-passwall2 | GPL-3.0 |
| LuCI Tailscale | https://github.com/asvow/luci-app-tailscale | GPL-3.0 |
| LuCI MosDNS | https://github.com/sbwml/luci-app-mosdns | GPL-3.0 |
| OpenWrt daed/daede | https://github.com/kenzok8/openwrt-daede | AGPL-3.0 |

This list is not a replacement for the package-level notices. Every Release includes the exact package versions, commit- or tag-pinned source locations, downloadable source archive URLs, source paths, and SPDX identifiers generated from the actual build manifest. Missing license or exact source metadata blocks publication.

The pinned archives, this repository's complete build scripts, and the recorded ImageBuilder/feed revisions are the documented network source-acquisition method for each published build. Recipients must follow the license terms of each component when redistributing firmware; the repository's MIT license does not relicense firmware components.

Some third-party feeds do not publish a cryptographically verifiable package-to-source-commit attestation. In those cases the build records an immutable source snapshot as `artifact_source_relation: unverified-upstream`; this is an explicit residual-risk disclosure, not a claim that the snapshot is proven to be the exact source used by upstream.
