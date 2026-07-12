# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Does

Single-workflow automation (`.github/workflows/build-openwrt.yml`): assembles ImmortalWrt x86_64 firmware via the official ImageBuilder, converts it to ESXi-importable OVA files, and publishes GitHub Releases containing the `.ova`, `.ova.sha256`, and raw `.img.gz` (for PVE `qm importdisk`). The workflow builds two sequential flavors from the same ImageBuilder: the standard bypass-router image and an independent `daed` image. Release tags and asset names include build date, ImmortalWrt commit, flavor, and image SHA. **Images never enter git** — only small conversion records are committed back to `main` with `[skip ci]`.

**Security: keep this repo private.** No secrets live in the codebase; if runtime config files are added later, keep them out of git.

## Commands

Dependencies (Ubuntu): `sudo apt-get install -y qemu-utils` (requires `qemu-img`).

```bash
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

1. **ImageBuilder assembly** — release defaults to `IB_VERSION=24.10.6`; manual dispatch can override `ib_version` and set `publish_release=false` for dry-run experiments. The workflow resolves the upstream `sha256sums` entry before downloading the ImageBuilder. Auxiliary image formats (ISO/qcow2/VDI/VMDK/VHDX) are sed-disabled in the IB `.config` because each needs extra host tools (xorriso, qemu-img) and nothing downstream consumes them. Packages are read from `config/openwrt-packages.txt` and `config/openwrt-packages-daed.txt`; `make manifest` runs for each flavor before `make image PROFILE=generic ROOTFS_PARTSIZE=2048 FILES=${GITHUB_WORKSPACE}/files PACKAGES=...`.
2. **Release metadata** — reads upstream `version.buildinfo` for values like `r33869-cf234f8de6d5`, extracts the ImmortalWrt commit, and combines it with the Asia/Shanghai build date for release tags and asset names like `openwrt-immortalwrt-x86-64-20260616-cf234f8de6d5-<image_sha12>` and `immortalwrt-x86-64-esxi-20260616-cf234f8de6d5-<image_sha12>.ova`.
3. **Third-party packages** — feeds are read from `config/third-party-feeds.tsv`, appended to `repositories.conf`, and probed via `Packages.gz`; signature checking is disabled because these third-party feeds are unsigned. Release assets are downloaded through `scripts/openwrt_build_preflight.py`, which requires exactly one asset match for PassWall2 LuCI, sbwml MosDNS, asvow Tailscale, and kenzok8 `luci-app-daede`. The `daed` core package comes from the ImmortalWrt 24.10.6 official package source, not kenzok8 release assets. Bypass-router tuning via `files/`: a sysctl overlay (BBR, raised conntrack limit, larger socket buffers, loose rp_filter, no redirects) and a `uci-defaults` script that disables DHCP/RA on LAN, sets hostname/timezone/NTP, selects Argon as the default LuCI theme, enables packet steering, keeps software flow offloading disabled by default for transparent-proxy safety, and enables irqbalance auto-start.
4. **Build flavors** — the job intentionally stays sequential rather than matrixed so manifest/docs updates cannot race. For each flavor, it reads that flavor's package file, runs `make manifest`, clears `bin/targets/x86/64`, then runs `make image`. Outputs are `build-out/immortalwrt-x86-64.img.gz` and `build-out/immortalwrt-x86-64-daed.img.gz`.
5. **Convert to OVA** — reuses `scripts/openwrt_img_to_ova.py scan` against `build-out/`, passing build date, ImmortalWrt version code, and commit metadata. The scan can emit standard and daed release records in one `dist/build-results.json`.
6. **Publish** — `scripts/openwrt_build_preflight.py copy-raw-images` copies each built raw image into `dist/` under its release asset name. `scripts/publish_releases.py` verifies local `.ova`, `.ova.sha256`, and renamed raw `.img.gz` assets, creates or updates the Release, verifies uploaded assets, then prunes old managed releases by family, keeping the latest 30 standard releases and latest 30 daed releases.
7. **Record** — manifest + docs are verified against every release tag built in the current run and the latest Release tag, then committed back to the branch.

History: source-builds of LEDE/ImmortalWrt were abandoned (6h GitHub-hosted job hard limit on 4-core runners; timeout cancellation also kills post-steps so build caches were never saved; LEDE has no ImageBuilder/binary repo). A separate push-triggered convert workflow existed when images were committed to `img/` via LFS — removed when images moved to Releases only.

### Conversion pipeline (`scripts/openwrt_img_to_ova.py`)

`build_image()` is the core flow:
1. Decompress `.img.gz` → raw image (gzip exit code 2 is tolerated for trailing garbage, logged as a warning).
2. `qemu-img convert` to streamOptimized IDE VMDK (`adapter_type=ide`).
3. Generate OVF (vmx-17, 2 vCPU/2GB, 1 VmxNet3 NIC via `--nic-count 1`; IDE disk controller; adjust the script flag for more NICs) + SHA256 manifest, tar them into an OVA, write an `.ova.sha256` checksum.

### Dedup / idempotency

Conversion key = `image_sha256:BUILDER_VERSION`. Keys live in `manifests/converted-images.json`; `scan` skips keys already present. **Bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` whenever conversion logic or release tag semantics change.**

Release cleanup is intentionally scoped to automatic standard and daed tags:

- `openwrt-immortalwrt-x86-64-<image_sha12>`
- `openwrt-immortalwrt-x86-64-<YYYYMMDD>-<commit>-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<image_sha12>`
- `openwrt-immortalwrt-x86-64-daed-<YYYYMMDD>-<commit>-<image_sha12>`

Do not broaden that matcher without an explicit reason. Pruning is family-aware: standard releases and daed releases each keep their own `--keep-releases` budget.

### Runtime notes

- Standard image keeps PassWall2, OpenClash, and Nikki/Mihomo. Momo is intentionally omitted because Nikki and Momo have conflicting transparent-proxy nft rules.
- Daed image is a separate flavor, not an addition to the standard image. It includes `luci-app-daede` and official-source `daed`, and explicitly omits `luci-app-passwall2`, `luci-app-openclash`, `luci-app-nikki`, `luci-i18n-nikki-zh-cn`, and `mihomo-meta`.
- Generated artifacts (`dist/`, `build-out/`, `imagebuilder/`, `*.ova`, `*.vmdk`, etc.) are gitignored.
