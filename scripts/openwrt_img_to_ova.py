#!/usr/bin/env python3
"""Build ESXi-ready OVA files from OpenWrt raw images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

BUILDER_VERSION = "8"


@dataclass(frozen=True)
class BuildResult:
    key: str
    release_tag: str
    release_title: str
    image_asset: str
    release_date: str | None
    immortalwrt_version_code: str | None
    immortalwrt_commit: str | None
    image_path: str
    image_sha256: str
    ova_path: str
    checksum_path: str
    ovf_path: str
    vmdk_path: str
    builder_version: str


def sanitize_name(value: str) -> str:
    clean = re.sub(r"(\.img\.gz|\.img)$", "", value)
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", clean).strip("-._")
    return clean or "openwrt"


def sanitize_tag_component(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if not clean:
        raise ValueError("release metadata contains an empty tag component")
    return clean


def release_display_name(base_name: str) -> str:
    if base_name == "immortalwrt-x86-64-daed":
        return "ImmortalWrt x86_64 daed"
    if base_name == "immortalwrt-x86-64":
        return "ImmortalWrt x86_64"
    return base_name


def release_metadata(
    base_name: str,
    short_image: str,
    *,
    release_date: str | None = None,
    immortalwrt_commit: str | None = None,
) -> tuple[str, str, str]:
    display_name = release_display_name(base_name)
    if release_date and immortalwrt_commit:
        release_date = sanitize_tag_component(release_date)
        immortalwrt_commit = sanitize_tag_component(immortalwrt_commit)
        release_tag = "openwrt-{base}-{date}-{commit}-{image}".format(
            base=base_name,
            date=release_date,
            commit=immortalwrt_commit,
            image=short_image,
        )
        artifact_name = f"{base_name}-{release_date}-{immortalwrt_commit}-{short_image}"
        release_title = f"{display_name} ESXi OVA - {release_date} {immortalwrt_commit}"
    else:
        release_tag = f"openwrt-{base_name}-{short_image}"
        release_title = f"{display_name} ESXi OVA ({short_image})"
        artifact_name = f"{base_name}-{short_image}"
    return release_tag, release_title, artifact_name


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path | None = None, allow_codes: set[int] | None = None) -> None:
    allowed = allow_codes or {0}
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")


def require_tools(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing required tools: {', '.join(missing)}")


def read_manifest(path: Path) -> dict:
    if not path.exists():
        return {"builder_version": BUILDER_VERSION, "conversions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def decompress_image(source: Path, target: Path) -> None:
    if source.name.endswith(".gz"):
        with target.open("wb") as dst:
            result = subprocess.run(
                ["gzip", "-cd", str(source)],
                stdout=dst,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode not in (0, 2):
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"failed to decompress {source}: {stderr}")
        if result.returncode == 2:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            print(f"warning: gzip reported trailing garbage for {source}: {stderr}")
    else:
        shutil.copy2(source, target)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"failed to prepare raw image from {source}")


def make_ovf(name: str, vmdk_name: str, vmdk_size: int, disk_capacity: int, nic_count: int) -> str:
    # InstanceID allocation: 0 system, 1 vCPU, 2 memory, 3 disk controller, 4 disk,
    # 5-7 reserved for future controllers, 8+ NICs. Keep NICs starting at 8 so any
    # additional controllers added below stay collision-free.
    network_section = "\n".join(
        f'    <Network ovf:name="LAN{i}"><Description>LAN adapter {i}</Description></Network>'
        for i in range(1, nic_count + 1)
    )
    nics = []
    instance = 8
    for i in range(1, nic_count + 1):
        nics.append(
            f"""      <Item>
        <rasd:AddressOnParent>{i - 1}</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>LAN{i}</rasd:Connection>
        <rasd:Description>VmxNet3 ethernet adapter on LAN{i}</rasd:Description>
        <rasd:ElementName>Network adapter {i}</rasd:ElementName>
        <rasd:InstanceID>{instance}</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>"""
        )
        instance += 1

    escaped_name = escape(name)
    escaped_vmdk = escape(vmdk_name)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
          xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
          xmlns:vmw="http://www.vmware.com/schema/ovf">
  <References>
    <File ovf:id="file1" ovf:href="{escaped_vmdk}" ovf:size="{vmdk_size}"/>
  </References>
  <DiskSection>
    <Info>Virtual disk information</Info>
    <Disk ovf:diskId="vmdisk1" ovf:fileRef="file1" ovf:capacity="{disk_capacity}" ovf:capacityAllocationUnits="byte * 2^0" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <NetworkSection>
    <Info>Logical networks</Info>
{network_section}
  </NetworkSection>
  <VirtualSystem ovf:id="{escaped_name}">
    <Info>OpenWrt virtual machine</Info>
    <Name>{escaped_name}</Name>
    <OperatingSystemSection ovf:id="100" vmw:osType="otherLinux64Guest">
      <Info>OpenWrt x86_64</Info>
      <Description>OpenWrt x86_64</Description>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemIdentifier>{escaped_name}</vssd:VirtualSystemIdentifier>
        <vssd:VirtualSystemType>vmx-17</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:Description>Number of virtual CPUs</rasd:Description>
        <rasd:ElementName>2 virtual CPU(s)</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>2</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Description>Memory Size</rasd:Description>
        <rasd:ElementName>2048MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>2048</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Description>IDE Controller</rasd:Description>
        <rasd:ElementName>IDE 0</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>ide</rasd:ResourceSubType>
        <rasd:ResourceType>5</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:ElementName>Hard disk 1</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/vmdisk1</rasd:HostResource>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
{os.linesep.join(nics)}
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def sha256_text_for(path: Path) -> str:
    return f"SHA256({path.name})= {sha256_file(path)}\n"


def write_ova(ova: Path, files: list[Path]) -> None:
    with tarfile.open(ova, "w") as tar:
        for path in files:
            tar.add(path, arcname=path.name)


def build_image(
    image: Path,
    out_dir: Path,
    nic_count: int,
    *,
    release_date: str | None = None,
    immortalwrt_version_code: str | None = None,
    immortalwrt_commit: str | None = None,
) -> BuildResult:
    require_tools(["qemu-img"])
    image_sha = sha256_file(image)
    base_name = sanitize_name(image.name)
    short_image = image_sha[:12]
    key = f"{image_sha}:{BUILDER_VERSION}"
    release_tag, release_title, artifact_name = release_metadata(
        base_name,
        short_image,
        release_date=release_date,
        immortalwrt_commit=immortalwrt_commit,
    )
    image_asset = f"{artifact_name}.img.gz"
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="openwrt-img-to-ova-") as tmp_s:
        tmp = Path(tmp_s)
        raw = tmp / f"{base_name}.img"
        esxi_artifact_name = f"{base_name}-esxi-{artifact_name.removeprefix(base_name + '-')}"
        vmdk = out_dir / f"{esxi_artifact_name}.vmdk"
        ovf = out_dir / f"{esxi_artifact_name}.ovf"
        mf = out_dir / f"{esxi_artifact_name}.mf"
        ova = out_dir / f"{esxi_artifact_name}.ova"
        checksum = out_dir / f"{ova.name}.sha256"

        decompress_image(image, raw)
        run(["qemu-img", "convert", "-f", "raw", "-O", "vmdk", "-o", "subformat=streamOptimized,adapter_type=ide", str(raw), str(vmdk)])
        disk_capacity = raw.stat().st_size
        ovf.write_text(make_ovf(base_name, vmdk.name, vmdk.stat().st_size, disk_capacity, nic_count), encoding="utf-8")
        mf.write_text(sha256_text_for(ovf) + sha256_text_for(vmdk), encoding="utf-8")
        write_ova(ova, [ovf, mf, vmdk])
        checksum.write_text(f"{sha256_file(ova)}  {ova.name}\n", encoding="utf-8")

    return BuildResult(
        key=key,
        release_tag=release_tag,
        release_title=release_title,
        image_asset=image_asset,
        release_date=release_date,
        immortalwrt_version_code=immortalwrt_version_code,
        immortalwrt_commit=immortalwrt_commit,
        image_path=relative_display_path(image),
        image_sha256=image_sha,
        ova_path=ova.as_posix(),
        checksum_path=checksum.as_posix(),
        ovf_path=ovf.as_posix(),
        vmdk_path=vmdk.as_posix(),
        builder_version=BUILDER_VERSION,
    )


def result_to_dict(result: BuildResult) -> dict[str, str]:
    return {
        "key": result.key,
        "release_tag": result.release_tag,
        "release_title": result.release_title,
        "image_asset": result.image_asset,
        "release_date": result.release_date or "",
        "immortalwrt_version_code": result.immortalwrt_version_code or "",
        "immortalwrt_commit": result.immortalwrt_commit or "",
        "image_path": result.image_path,
        "image_sha256": result.image_sha256,
        "ova_path": result.ova_path,
        "checksum_path": result.checksum_path,
        "ovf_path": result.ovf_path,
        "vmdk_path": result.vmdk_path,
        "builder_version": result.builder_version,
    }


def relative_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_images(img_dir: Path) -> list[Path]:
    patterns = ("*.img", "*.img.gz")
    images: list[Path] = []
    for pattern in patterns:
        images.extend(img_dir.rglob(pattern))
    return sorted({path.resolve() for path in images if path.is_file()})


def validate_release_metadata(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    release_date = args.release_date
    immortalwrt_version_code = args.immortalwrt_version_code
    immortalwrt_commit = args.immortalwrt_commit
    if not any((release_date, immortalwrt_version_code, immortalwrt_commit)):
        return None, None, None
    if not release_date or not immortalwrt_commit:
        raise ValueError("--release-date and --immortalwrt-commit must be provided together")
    if not re.fullmatch(r"\d{8}", release_date):
        raise ValueError("--release-date must use YYYYMMDD format")
    return release_date, immortalwrt_version_code, immortalwrt_commit


def cmd_scan(args: argparse.Namespace) -> int:
    manifest = read_manifest(args.manifest)
    conversions = manifest.get("conversions", {})
    release_date, immortalwrt_version_code, immortalwrt_commit = validate_release_metadata(args)
    built: list[dict[str, str]] = []
    skipped = 0
    for image in discover_images(args.img_dir):
        image_sha = sha256_file(image)
        key = f"{image_sha}:{BUILDER_VERSION}"
        if key in conversions:
            skipped += 1
            print(f"skip already converted: {image}")
            continue
        print(f"build pending image: {image}")
        built.append(
            result_to_dict(
                build_image(
                    image,
                    args.out_dir,
                    args.nic_count,
                    release_date=release_date,
                    immortalwrt_version_code=immortalwrt_version_code,
                    immortalwrt_commit=immortalwrt_commit,
                )
            )
        )
    write_json(args.results, {"built": built, "skipped": skipped})
    print(f"built {len(built)} image(s), skipped {skipped}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    results = json.loads(args.results.read_text(encoding="utf-8"))
    manifest = read_manifest(args.manifest)
    conversions = manifest.setdefault("conversions", {})
    for item in results.get("built", []):
        conversions[item["key"]] = {
            "image_path": item["image_path"],
            "image_asset": item.get("image_asset", Path(item["image_path"]).name),
            "image_sha256": item["image_sha256"],
            "builder_version": item["builder_version"],
            "release_tag": item["release_tag"],
            "release_date": item.get("release_date"),
            "immortalwrt_version_code": item.get("immortalwrt_version_code"),
            "immortalwrt_commit": item.get("immortalwrt_commit"),
            "ova_asset": Path(item["ova_path"]).name,
            "checksum_asset": Path(item["checksum_path"]).name,
        }
    manifest["builder_version"] = BUILDER_VERSION
    write_json(args.manifest, manifest)
    write_converted_doc(args.doc, manifest)
    return 0


def write_converted_doc(path: Path, manifest: dict) -> None:
    rows = []
    conversions = manifest.get("conversions", {})
    for item in sorted(conversions.values(), key=lambda value: value["release_tag"]):
        rows.append(
            "| `{release_tag}` | `{date}` | `{version_code}` | `{commit}` | `{image}` | `{image_sha}` | `{builder}` |".format(
                release_tag=item["release_tag"],
                date=item.get("release_date") or "_unknown_",
                version_code=item.get("immortalwrt_version_code") or "_unknown_",
                commit=item.get("immortalwrt_commit") or "_unknown_",
                image=item.get("image_asset") or Path(item["image_path"]).name,
                image_sha=item["image_sha256"][:12],
                builder=item["builder_version"],
            )
        )
    content = [
        "# Converted Images",
        "",
        "This file is generated by the GitHub Actions workflow.",
        "",
        "| Release | Build Date | ImmortalWrt Version | ImmortalWrt Commit | Image | Image SHA | Builder |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *(rows or ["| _None_ | _None_ | _None_ | _None_ | _None_ | _None_ | _None_ |"]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(content), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="build all unconverted images")
    scan.add_argument("--img-dir", type=Path, default=Path("img"))
    scan.add_argument("--manifest", type=Path, default=Path("manifests/converted-images.json"))
    scan.add_argument("--out-dir", type=Path, default=Path("dist"))
    scan.add_argument("--results", type=Path, default=Path("dist/build-results.json"))
    scan.add_argument("--nic-count", type=int, default=6)
    scan.add_argument("--release-date", help="build/release date in YYYYMMDD format")
    scan.add_argument("--immortalwrt-version-code", help="ImmortalWrt version.buildinfo value")
    scan.add_argument("--immortalwrt-commit", help="ImmortalWrt source commit id")
    scan.set_defaults(func=cmd_scan)

    record = subparsers.add_parser("record", help="record successfully published builds")
    record.add_argument("--results", type=Path, default=Path("dist/build-results.json"))
    record.add_argument("--manifest", type=Path, default=Path("manifests/converted-images.json"))
    record.add_argument("--doc", type=Path, default=Path("docs/converted-images.md"))
    record.set_defaults(func=cmd_record)

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
