# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Does

Single-workflow automation (`.github/workflows/build-openwrt.yml`): assembles ImmortalWrt x86_64 firmware via the official ImageBuilder, converts it to an ESXi-importable OVA, and publishes a GitHub Release containing the `.ova`, `.ova.sha256`, and raw `.img.gz` (for PVE `qm importdisk`). Release tags and asset names include build date, ImmortalWrt commit, and image SHA. **Images never enter git** — only small conversion records are committed back to `main` with `[skip ci]`.

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

There are no tests or linters configured. Scripts are stdlib-only Python 3 (no pip dependencies).

## Architecture

### Build + release pipeline (`.github/workflows/build-openwrt.yml`)

One job, triggered by `workflow_dispatch` or a daily schedule. Steps in order:

1. **ImageBuilder assembly** — release pinned via `IB_VERSION` env (e.g. `24.10.6`); auxiliary image formats (ISO/qcow2/VDI/VMDK/VHDX) are sed-disabled in the IB `.config` because each needs extra host tools (xorriso, qemu-img) and nothing downstream consumes them. Image built with `make image PROFILE=generic ROOTFS_PARTSIZE=4096 FILES=${GITHUB_WORKSPACE}/files PACKAGES=...` — **a nonexistent package name fails the build loudly**.
2. **Release metadata** — reads upstream `version.buildinfo` for values like `r33869-cf234f8de6d5`, extracts the ImmortalWrt commit, and combines it with the Asia/Shanghai build date for release tags and asset names like `openwrt-immortalwrt-x86-64-20260616-cf234f8de6d5-<image_sha12>` and `immortalwrt-x86-64-esxi-20260616-cf234f8de6d5-<image_sha12>.ova`.
3. **Third-party packages** — feeds appended to `repositories.conf` (Nikki pages.dev and passwall2 SourceForge; signature checking disabled; appended idempotently and probed for reachability since SourceForge intermittently fails from runners); release ipks downloaded into `packages/` (sbwml MosDNS offline bundle — chosen over the kiddin9 dl.openwrt.ai feed which intermittently fails from runners; asvow `luci-app-tailscale`). The ImageBuilder tree and `packages/` are cached via `actions/cache` keyed on `IB_VERSION` / workflow hash to skip re-downloads on daily runs. Bypass-router tuning via `files/`: a sysctl overlay (BBR, raised conntrack limit, larger socket buffers, loose rp_filter, no redirects) and a `uci-defaults` script that disables DHCP/RA on LAN, sets hostname/timezone/NTP, selects Argon as the default LuCI theme, enables packet steering, enables software flow offloading (`firewall.flow_offloading=1`, no HW offload for virtual NICs), and enables irqbalance auto-start.
4. **Convert to OVA** — reuses `scripts/openwrt_img_to_ova.py scan` against `build-out/`, passing build date, ImmortalWrt version code, and commit metadata.
5. **Publish** — `scripts/publish_releases.py` creates the Release (skips existing tags, so re-runs are safe), prunes old managed releases to keep the latest 30, then `gh release upload` attaches the raw `.img.gz`.
6. **Record** — manifest + docs committed back to the branch.

History: source-builds of LEDE/ImmortalWrt were abandoned (6h GitHub-hosted job hard limit on 4-core runners; timeout cancellation also kills post-steps so build caches were never saved; LEDE has no ImageBuilder/binary repo). A separate push-triggered convert workflow existed when images were committed to `img/` via LFS — removed when images moved to Releases only.

### Conversion pipeline (`scripts/openwrt_img_to_ova.py`)

`build_image()` is the core flow:
1. Decompress `.img.gz` → raw image (gzip exit code 2 is tolerated for trailing garbage, logged as a warning).
2. `qemu-img convert` to streamOptimized **LSI Logic SAS** VMDK (`adapter_type=lsisas1068`).
3. Generate OVF (vmx-17, 2 vCPU/2GB, 1 VmxNet3 NIC via `--nic-count 1`; LSI Logic SAS disk controller; adjust the script flag for more NICs) + SHA256 manifest, tar them into an OVA, write an `.ova.sha256` checksum.

### Dedup / idempotency

Conversion key = `image_sha256:BUILDER_VERSION`. Keys live in `manifests/converted-images.json`; `scan` skips keys already present. **Bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` whenever conversion logic or release tag semantics change.**

Release cleanup is intentionally scoped to automatic tags matching `openwrt-immortalwrt-x86-64-<image_sha12>` or `openwrt-immortalwrt-x86-64-<YYYYMMDD>-<commit>-<image_sha12>`. Do not broaden that matcher without an explicit reason.

### Runtime notes

- Nikki is installed as the retained transparent-proxy stack; Momo is intentionally omitted because Nikki and Momo have conflicting transparent-proxy nft rules.
- Generated artifacts (`dist/`, `build-out/`, `imagebuilder/`, `*.ova`, `*.vmdk`, etc.) are gitignored.
