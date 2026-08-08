# Third-party notices

This repository's MIT license applies only to the original automation, configuration, and documentation in this repository. Built firmware contains separately licensed software.

Primary upstream projects include:

| Component | Source | Declared license |
| --- | --- | --- |
| ImmortalWrt | https://github.com/immortalwrt/immortalwrt | GPL-2.0 build environment; packages vary |
| ImmortalWrt shellsync | https://github.com/immortalwrt/immortalwrt/tree/cd0a06bfd3fdbc1011e32d35348d2ee013b4daf2/package/network/services/shellsync | GPL-2.0-only under repository `COPYING` |
| Broadcom bnx2 firmware | https://gitlab.com/kernel-firmware/linux-firmware/-/tree/20260221/bnx2 | Broadcom redistribution notice in `WHENCE` |
| Intel i915 firmware | https://gitlab.com/kernel-firmware/linux-firmware/-/tree/20260221/i915 | Intel binary firmware license in `LICENSE.i915` |
| OpenClash | https://github.com/vernesong/OpenClash | MIT |
| OpenWrt Nikki | https://github.com/nikkinikki-org/OpenWrt-nikki | GPL-3.0 |
| PassWall 2 | https://github.com/Openwrt-Passwall/openwrt-passwall2 | GPL-3.0 |
| LuCI Tailscale | https://github.com/asvow/luci-app-tailscale | GPL-3.0 |
| LuCI MosDNS | https://github.com/sbwml/luci-app-mosdns | GPL-3.0 |
| OpenWrt daed/daede | https://github.com/kenzok8/openwrt-daede | AGPL-3.0 |

This list is not a replacement for the package-level notices. Every Release includes the exact package versions, commit- or tag-pinned source locations, downloadable source archive URLs, source paths, and SPDX identifiers generated from the actual build manifest. Missing license or exact source metadata blocks publication.

The pinned archives, this repository's complete build scripts, and the recorded ImageBuilder/feed revisions are the documented network source-acquisition method for each published build. Recipients must follow the license terms of each component when redistributing firmware; the repository's MIT license does not relicense firmware components.

Some third-party feeds do not publish a cryptographically verifiable package-to-source-commit attestation. In those cases the build records an immutable source snapshot as `artifact_source_relation: unverified-upstream`; this is an explicit residual-risk disclosure, not a claim that the snapshot is proven to be the exact source used by upstream.

The reviewed bnx2 and i915 notices above apply to linux-firmware version `20260221`. The metadata generator requires the corresponding installed package versions to match `20260221-*`; a future ImageBuilder update is blocked until its firmware source and license records are reviewed and updated.

ImmortalWrt 25.12.1 package metadata omits a `License` field for `shellsync`, which is installed as a hard dependency of `ppp`. The reviewed source has no separate license declaration, while the same fixed repository commit's [`COPYING`](https://github.com/immortalwrt/immortalwrt/blob/cd0a06bfd3fdbc1011e32d35348d2ee013b4daf2/COPYING) declares GPL-2.0-only and states that all contributions are subject to it. The override is limited to package versions matching `0.2-*`; a version or source revision change blocks publication pending review.
