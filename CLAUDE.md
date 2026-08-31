# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Does

Single-workflow automation (`.github/workflows/build-openwrt.yml`): assembles ImmortalWrt 25.12.1 x86_64 firmware via the official ImageBuilder, converts it to ESXi-importable OVA files, and publishes GitHub Releases containing the `.ova`, `.ova.sha256`, and raw `.img.gz` (for PVE `qm importdisk`). The workflow builds a single flavor declared in `config/build-profile.json` — official base packages plus daed, Tailscale, vnStat and open-vm-tools — resolved entirely from the official signed ImmortalWrt feeds. The profile `name` (`immortalwrt-x86-64-bypass`) names the whole package set, not one component, and drives the image filename, release tag family, and asset names. Release tags and asset names include build date, ImmortalWrt commit, and image SHA. **Images never enter git** — only small conversion records are committed back to `main` with `[skip ci]`.

**Security: this repository is published as public source.** No secrets live in the codebase; if runtime config files are added later, keep them out of git.

## Commands

Dependencies (Ubuntu): `sudo apt-get install -y qemu-utils` (requires `qemu-img`).

```bash
# Validate the single build profile and print the values the workflow exports
python3 scripts/openwrt_build_preflight.py validate-profile \
  --config config/build-profile.json

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

One job, triggered by `workflow_dispatch` or a daily schedule (02:00 Asia/Shanghai). Steps in order:

1. **ImageBuilder assembly** — defaults to `IB_VERSION=25.12.1`, `IB_TARGET=x86/64` (APK-based); manual dispatch can override `ib_version`, pick the `runner` (`ubuntu-latest` or `self-hosted`; scheduled runs always use `ubuntu-latest`), and set `publish_release=false` for dry-run experiments. A cleanup step removes `imagebuilder/`, `build-out/`, `dist/`, and the IB archive first because self-hosted workspaces persist between runs. The workflow resolves the upstream `sha256sums` entry before downloading the ImageBuilder. Auxiliary image formats (ISO/qcow2/VDI/VMDK/VHDX) are sed-disabled in the IB `.config` because each needs extra host tools (xorriso, qemu-img) and nothing downstream consumes them.
2. **Release metadata** — reads upstream `version.buildinfo` for values like `r37978-cd0a06bfd3fd`, extracts the ImmortalWrt commit, and combines it with the Asia/Shanghai build date for release tags and asset names like `openwrt-immortalwrt-x86-64-bypass-20260831-cd0a06bfd3fd-<image_sha12>` and `immortalwrt-x86-64-bypass-esxi-20260831-cd0a06bfd3fd-<image_sha12>.ova`.
3. **Packages** — every package resolves from the official signed ImmortalWrt 25.12.1 feeds. There is no third-party APK feed and no fallback download path; do not reintroduce one. The full set lives in `config/build-profile.json` (`packages`), gated by `required_packages` and `forbidden_packages`. `validate-profile` exports `BUILD_PROFILE`, `IMAGE_NAME`, `ROOTFS_PARTSIZE`, `NIC_COUNT`, `IMAGE_GLOB`, and `BUILD_PACKAGES` into `GITHUB_ENV`.
4. **Build** — `make manifest PROFILE="${BUILD_PROFILE}" PACKAGES="${BUILD_PACKAGES}"` first, then `validate-manifest` asserts every `required_packages` entry is present and rejects `forbidden_packages` (the retired `luci-app-daede` plus PassWall2, OpenClash, Nikki/Mihomo, and MosDNS). Then it clears `bin/targets/x86/64` and runs `make image PROFILE=... FILES=${GITHUB_WORKSPACE}/files ROOTFS_PARTSIZE=... PACKAGES=...`. `collect-image-outputs` copies the single matching image to `build-out/${IMAGE_NAME}.img.gz` and requires exactly one `.manifest` and one `.bom.cdx.json`. Bypass-router tuning via `files/`: a sysctl overlay (BBR, raised conntrack limit, larger socket buffers, loose rp_filter, no redirects) and `uci-defaults` scripts that disable DHCP/RA on LAN, set hostname/timezone/NTP, select Argon as the default LuCI theme, enable packet steering, keep software flow offloading disabled for transparent-proxy safety, enable irqbalance, and leave daed disabled until the setup wizard provisions it.
5. **Convert to OVA** — reuses `scripts/openwrt_img_to_ova.py scan` against `build-out/`, passing build date, ImmortalWrt version code, and commit metadata.
6. **Publish** — `scripts/openwrt_img_to_ova.py prepare-assets` renames the raw image into `dist/` under its release asset name and assembles the auditable payload (`SHA256SUMS`, `build-metadata.json`, `build-metadata.tar.gz`, manifest, SBOM, setup wizard). `scripts/publish_releases.py` verifies every declared asset, creates or verifies the Release, then prunes old managed releases by family (bypass keeps the latest 30; the legacy daed and standard matchers are retained so historical releases keep getting pruned).
7. **Record** — manifest + docs are verified against every release tag built in the current run and the latest Release tag, then committed back to the branch.

History: source-builds of LEDE/ImmortalWrt were abandoned (6h GitHub-hosted job hard limit on 4-core runners; timeout cancellation also kills post-steps so build caches were never saved; LEDE has no ImageBuilder/binary repo). A separate push-triggered convert workflow existed when images were committed to `img/` via LFS — removed when images moved to Releases only. The two-flavor 24.10.6 pipeline (standard + daed, third-party TSV feeds and release-asset ipks) was replaced by a single-flavor 25.12.1 pipeline. That flavor was called `daed` until the profile also absorbed Tailscale, vnStat and open-vm-tools, at which point it was renamed `bypass` because the tag no longer described one component.

### Conversion pipeline (`scripts/openwrt_img_to_ova.py`)

`build_image()` is the core flow:
1. Decompress `.img.gz` → raw image (gzip exit code 2 is tolerated for trailing garbage, logged as a warning).
2. `qemu-img convert` to streamOptimized IDE VMDK (`adapter_type=ide`).
3. Generate OVF (vmx-17, 2 vCPU/2GB, 1 VmxNet3 NIC via `--nic-count 1`; IDE disk controller; adjust the script flag for more NICs) + SHA256 manifest, tar them into an OVA, write an `.ova.sha256` checksum.

### Dedup / idempotency

Conversion key = `image_sha256:BUILDER_VERSION`, where `image_sha256` is the SHA256 of the whole built `.img.gz`, so any package change that lands in the rootfs changes it. Keys live in `manifests/converted-images.json`; `scan` skips keys already present, which drives the `count` output that gates the `publish` job. **Bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` whenever conversion logic or release tag semantics change.** It was bumped to 12 when the profile name became `immortalwrt-x86-64-bypass`.

Because the keys are committed, copying `manifests/converted-images.json` into another repository makes that repository treat the same image as already published, and `publish` silently skips. Prune foreign records when mirroring the tree.

Release cleanup is intentionally scoped to automatic bypass, daed, and standard tags:

- `openwrt-immortalwrt-x86-64-<image_sha12>`
- `openwrt-immortalwrt-x86-64-<YYYYMMDD>-<commit>-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<YYYYMMDD>-<commit>-<image_sha12>`
- `openwrt-immortalwrt-x86-64-bypass-<image_sha12>`
- `openwrt-immortalwrt-x86-64-bypass-<YYYYMMDD>-<commit>-<image_sha12>`

Do not broaden that matcher without an explicit reason. Pruning is family-aware: each family keeps its own `--keep-releases` budget. Renaming the profile requires adding both a `RELEASE_TAG_FAMILY_PATTERNS` entry and a `RELEASE_ASSET_FAMILY_PREFIXES` entry (longest prefix first) in `scripts/publish_releases.py`.

### Runtime notes

- The image ships one transparent proxy stack: the official `daed` package with the official `luci-app-daed` frontend and `daed-geoip`/`daed-geosite` rule sets. PassWall2, OpenClash, Nikki/Mihomo, and MosDNS are intentionally omitted so no conflicting transparent-proxy nft/eBPF rules ship by default. The retired third-party `luci-app-daede` is listed in `forbidden_packages`.
- daed ships disabled. `files/etc/uci-defaults/99-bypass-router.sh` only writes non-identity defaults (listen addr `0.0.0.0:2023`, log rotation); the administrator, password, and subscription are provisioned by `scripts/setup-openwrt.sh` at deploy time.
- Also bundled: Tailscale (with a dedicated firewall zone and `tailscale0` device match), vnStat 2 with the LuCI app and `sqlite3-cli`, and `open-vm-tools` for VMware guest integration.
- Generated artifacts (`dist/`, `build-out/`, `imagebuilder/`, `*.ova`, `*.vmdk`, etc.) are gitignored.
