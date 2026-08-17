# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Does

Single-workflow automation (`.github/workflows/build-openwrt.yml`): assembles ImmortalWrt 25.12.1 x86_64 firmware via the official ImageBuilder, converts it to ESXi-importable OVA files, and publishes GitHub Releases containing the `.ova`, `.ova.sha256`, and raw `.img.gz` (for PVE `qm importdisk`). The workflow builds a single daed flavor: official base packages plus `daed` and `luci-app-daede` from the kenzok8 daed feed. Release tags and asset names include build date, ImmortalWrt commit, and image SHA. **Images never enter git** — only small conversion records are committed back to `main` with `[skip ci]`.

**Security: keep this repo private.** No secrets live in the codebase; if runtime config files are added later, keep them out of git.

## Commands

Dependencies (Ubuntu): `sudo apt-get install -y qemu-utils` (requires `qemu-img`).

```bash
# Fetch sha256-verified daed apk packages into the ImageBuilder local package dir
python3 scripts/openwrt_build_preflight.py daed-packages \
  --config config/daed-feed.json \
  --out-dir imagebuilder/packages \
  --metadata-out dist/daed-packages.json \
  --retries 8 --timeout 300

# Convert OpenWrt images in a directory to OVA (output to dist/)
python3 scripts/openwrt_img_to_ova.py scan \
  --img-dir <dir-with-img-files> \
  --manifest manifests/converted-images.json \
  --out-dir dist \
  --results dist/build-results.json \
  --nic-count 1 \
  --release-date 20260616 \
  --immortalwrt-version-code r33869-cf234f8de6d5 \
  --immortalwrt-commit cf234f8de6d5

# Record published builds into manifest + docs (normally done by CI)
python3 scripts/openwrt_img_to_ova.py record \
  --results dist/build-results.json \
  --manifest manifests/converted-images.json \
  --doc docs/converted-images.md
```

Scripts are stdlib-only Python 3 (no pip dependencies). Run these checks before handing off script or workflow changes:

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-openwrt.yml"); puts "workflow yaml ok"'
```

## Architecture

### Build + release pipeline (`.github/workflows/build-openwrt.yml`)

One job, triggered by `workflow_dispatch` or a daily schedule. Steps in order:

1. **ImageBuilder assembly** — defaults to `IB_VERSION=25.12.1`, `IB_TARGET=x86/64` (APK-based); manual dispatch can override `ib_version` and set `publish_release=false` for dry-run experiments. The workflow resolves the upstream `sha256sums` entry before downloading the ImageBuilder. Auxiliary image formats (ISO/qcow2/VDI/VMDK/VHDX) are sed-disabled in the IB `.config` because each needs extra host tools (xorriso, qemu-img) and nothing downstream consumes them.
2. **Release metadata** — reads upstream `version.buildinfo` for values like `r33869-cf234f8de6d5`, extracts the ImmortalWrt commit, and combines it with the Asia/Shanghai build date for release tags and asset names like `openwrt-immortalwrt-x86-64-daed-20260616-cf234f8de6d5-<image_sha12>` and `immortalwrt-x86-64-daed-esxi-20260616-cf234f8de6d5-<image_sha12>.ova`.
3. **Third-party packages** — only `daed` and `luci-app-daede` come from outside official feeds. `scripts/openwrt_build_preflight.py daed-packages` reads `config/daed-feed.json` (feed base `https://down.dllkids.xyz/openwrt-feed/daed`, sdk `25.12`, arch `x86_64`), parses `manifest-daede.txt` for filename + sha256 pairs, downloads the required `.apk` files into `imagebuilder/packages/` with sha256 verification, and records the pinned version/sha256/url into `dist/daed-packages.json`. The ImageBuilder auto-indexes local `packages/*.apk` (its Makefile runs `apk mkndx`) and keeps signature checking enabled; the feed itself is unsigned, so it is never added to `repositories`. All other dependencies (`v2ray-geoip`, `v2ray-geosite`, `kmod-sched-core`, `kmod-sched-bpf`, `kmod-veth`, `ca-bundle`, luci packages, vlmcsd) resolve from the official 25.12.1 feeds. Changing `config/daed-feed.json` is security-sensitive; its `pin` field can lock an exact filename when a kenzok8 update breaks dependency resolution.
4. **Build** — `make manifest PROFILE=generic PACKAGES="${DAED_PACKAGES}"` first; the workflow asserts the manifest contains `daed`, `luci-app-daede`, and `luci-app-vlmcsd` and rejects forbidden packages (`luci-app-passwall2`, `luci-app-openclash`, `luci-app-nikki`, `luci-i18n-nikki-zh-cn`, `nikki`, `mihomo-meta`, `luci-app-mosdns`, `mosdns`). Then it clears `bin/targets/x86/64` and runs `make image PROFILE=generic FILES=${GITHUB_WORKSPACE}/files ROOTFS_PARTSIZE=2048 PACKAGES=...`. Output is `build-out/immortalwrt-x86-64-daed.img.gz`. `DAED_PACKAGES` is `luci luci-i18n-base-zh-cn luci-theme-argon luci-app-vlmcsd daed luci-app-daede`. Bypass-router tuning via `files/`: a sysctl overlay (BBR, raised conntrack limit, larger socket buffers, loose rp_filter, no redirects) and a `uci-defaults` script that disables DHCP/RA on LAN, sets hostname/timezone/NTP, selects Argon as the default LuCI theme, enables packet steering, keeps software flow offloading disabled by default for transparent-proxy safety, and enables irqbalance auto-start.
5. **Convert to OVA** — reuses `scripts/openwrt_img_to_ova.py scan` against `build-out/`, passing build date, ImmortalWrt version code, and commit metadata.
6. **Publish** — `scripts/openwrt_build_preflight.py copy-raw-images` copies the built raw image into `dist/` under its release asset name. `scripts/publish_releases.py` verifies local `.ova`, `.ova.sha256`, and renamed raw `.img.gz` assets, creates or updates the Release, verifies uploaded assets, then prunes old managed releases by family (daed family keeps the latest 30; the legacy standard family matcher is retained so historical standard releases keep getting pruned).
7. **Record** — manifest + docs are verified against every release tag built in the current run and the latest Release tag, then committed back to the branch.

History: source-builds of LEDE/ImmortalWrt were abandoned (6h GitHub-hosted job hard limit on 4-core runners; timeout cancellation also kills post-steps so build caches were never saved; LEDE has no ImageBuilder/binary repo). A separate push-triggered convert workflow existed when images were committed to `img/` via LFS — removed when images moved to Releases only. The two-flavor 24.10.6 pipeline (standard + daed, third-party TSV feeds and release-asset ipks) was replaced in favor of the 25.12.1 daed-only pipeline.

### Conversion pipeline (`scripts/openwrt_img_to_ova.py`)

`build_image()` is the core flow:
1. Decompress `.img.gz` → raw image (gzip exit code 2 is tolerated for trailing garbage, logged as a warning).
2. `qemu-img convert` to streamOptimized IDE VMDK (`adapter_type=ide`).
3. Generate OVF (vmx-17, 2 vCPU/2GB, 1 VmxNet3 NIC via `--nic-count 1`; IDE disk controller; adjust the script flag for more NICs) + SHA256 manifest, tar them into an OVA, write an `.ova.sha256` checksum.

### Dedup / idempotency

Conversion key = `image_sha256:BUILDER_VERSION`. Keys live in `manifests/converted-images.json`; `scan` skips keys already present. **Bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` whenever conversion logic or release tag semantics change.** It was bumped to 9 when the pipeline moved to ImmortalWrt 25.12.1 daed-only.

Release cleanup is intentionally scoped to automatic standard and daed tags:

- `openwrt-immortalwrt-x86-64-<image_sha12>`
- `openwrt-immortalwrt-x86-64-<YYYYMMDD>-<commit>-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<YYYYMMDD>-<commit>-<image_sha12>`

Do not broaden that matcher without an explicit reason. Pruning is family-aware: standard releases and daed releases each keep their own `--keep-releases` budget.

### Runtime notes

- The image ships one transparent proxy stack: `daed` (dae core + dae-wing + embedded web UI) with the `luci-app-daede` LuCI frontend. PassWall2, OpenClash, Nikki/Mihomo, and MosDNS are intentionally omitted so no conflicting transparent-proxy nft/eBPF rules ship by default.
- `dae` (standalone core package) is not installed; the daed apk bundles its own core. `luci-app-daede` depends on `daed`, which depends on `libc`, `ca-bundle`, `kmod-sched-core`, `kmod-sched-bpf`, `kmod-veth`, `v2ray-geoip`, `v2ray-geosite` — all satisfied by official 25.12.1 feeds (verified against the x86/64 release kmods/packages feeds).
- The kenzok8 daed feed is version-rolling (e.g. `daed-2026.08.13-r2.apk`); builds follow `manifest-daede.txt` automatically and abort on sha256 mismatch. If a rolling update breaks `make image` dependency resolution, pin the previous filename in `config/daed-feed.json` `pin`.
- Generated artifacts (`dist/`, `build-out/`, `imagebuilder/`, `*.ova`, `*.vmdk`, etc.) are gitignored.
