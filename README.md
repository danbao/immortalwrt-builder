# OpenWrt IMG to ESXi OVA

Automation that assembles ImmortalWrt x86_64 firmware with the official ImageBuilder, converts it to an ESXi-importable OVA, and publishes everything as GitHub Releases. Images are never committed to git.

## Workflow

`.github/workflows/build-openwrt.yml` runs the whole pipeline in one job (manual dispatch or weekly schedule, Sunday 02:00 Asia/Shanghai):

1. Assemble an x86_64 squashfs UEFI image with the **ImmortalWrt ImageBuilder** (prebuilt packages, no compilation — minutes per run). The ImageBuilder release is pinned via the `IB_VERSION` workflow env; auxiliary image formats (ISO/qcow2/VDI/VMDK/VHDX) are disabled.
2. Convert the image to a streamOptimized VMDK wrapped as OVA (`scripts/openwrt_img_to_ova.py`).
3. Publish a GitHub Release containing the `.ova`, its `.sha256`, and the raw `.img.gz`.
4. Commit conversion records (`manifests/converted-images.json`, `docs/converted-images.md`) back to `main`; the same image/builder tuple is never converted twice.

Bundled packages: PassWall 2, MosDNS, OpenClash, vlmcsd (KMS), Nikki, Momo, Tailscale, ZeroTier, plus bypass-router tuning (BBR, raised conntrack limit, larger socket buffers, loose rp_filter, nftables flow offload, irqbalance, UPnP, `luci-app-statistics`) and diagnostics tools. Packages missing from the official repo come from third-party prebuilt feeds (nikki/momo pages.dev, passwall SourceForge) and release ipks (sbwml MosDNS bundle, asvow `luci-app-tailscale`).

Note: Nikki and Momo are both included in the firmware but their transparent-proxy nftables rules conflict — enable only one at runtime.

Bypass-router defaults baked into the image: forwarding sysctl tuning (see `files/etc/sysctl.d/` in the workflow) and a `uci-defaults` script that disables DHCP/RA on LAN (the main router owns address assignment). After importing, set a LAN IP that does not collide with your main router.

## Importing

- **ESXi**: download the `.ova` from the Release and import it via the UI (2 vCPU / 2GB / 1 VmxNet3 NIC).
- **PVE**: download the raw `.img.gz`, decompress, then `qm importdisk <vmid> immortalwrt-x86-64.img <storage>` (raw format is supported natively).

## Local Conversion

Ubuntu dependencies:

```bash
sudo apt-get install -y qemu-utils
```

Convert any OpenWrt image locally:

```bash
python3 scripts/openwrt_img_to_ova.py scan \
  --img-dir <dir-with-img-files> \
  --manifest manifests/converted-images.json \
  --out-dir dist \
  --results dist/build-results.json
```

Generated OVA files are written to `dist/`.

## Security Notes

Keep this repository private. If you ever inject runtime configuration files (e.g. into `files/`), make sure they contain no secrets before committing.
