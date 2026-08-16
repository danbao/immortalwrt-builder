#!/usr/bin/env python3
"""Publish built OVA files to GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RELEASE_TAG_FAMILY_PATTERNS = (
    ("base", re.compile(r"^openwrt-immortalwrt-x86-generic-(?:[0-9a-f]{12}|\d{8}-[0-9a-f]+-[0-9a-f]{12})$")),
)
ASSET_NAME_PATTERN = re.compile(
    r"^(?:immortalwrt-x86-generic.*(\.img\.gz|\.ova|\.ova\.sha256)|"
    r"immortalwrt-x86-generic.*\.img\.gz\.sha256|"
    r"build-metadata\.json|packages\.spdx\.json|upstream-provenance\.json|"
    r"source-inventory\.json)$"
)
METADATA_ASSET_NAMES = (
    "build-metadata.json",
    "packages.spdx.json",
    "upstream-provenance.json",
    "source-inventory.json",
)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def release_exists(tag: str) -> bool:
    result = run(["gh", "release", "view", tag], check=False)
    return result.returncode == 0


def is_managed_release_tag(tag: str) -> bool:
    return managed_release_family(tag) is not None


def managed_release_family(tag: str) -> str | None:
    for family, pattern in RELEASE_TAG_FAMILY_PATTERNS:
        if pattern.fullmatch(tag):
            return family
    return None


def managed_release_image_key(tag: str) -> tuple[str, str] | None:
    family = managed_release_family(tag)
    if family is None:
        return None
    return family, tag.rsplit("-", 1)[-1]


def is_managed_asset_name(name: str) -> bool:
    return bool(ASSET_NAME_PATTERN.fullmatch(name))


def list_release_assets(tag: str) -> list[dict[str, object]]:
    result = run(["gh", "release", "view", tag, "--json", "assets"], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    payload = json.loads(result.stdout or "{}")
    return payload.get("assets", [])


def release_asset_names(tag: str) -> set[str]:
    return {str(asset.get("name", "")) for asset in list_release_assets(tag)}


def existing_release_image_sha256(tag: str) -> str | None:
    if "build-metadata.json" not in release_asset_names(tag):
        return None
    with tempfile.TemporaryDirectory() as tmp_s:
        result = run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--pattern",
                "build-metadata.json",
                "--dir",
                tmp_s,
                "--clobber",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        payload = json.loads((Path(tmp_s) / "build-metadata.json").read_text(encoding="utf-8"))
    matches = [
        item
        for item in payload.get("results", {}).get("built", [])
        if isinstance(item, dict) and item.get("release_tag") == tag
    ]
    if len(matches) != 1:
        raise ValueError(f"existing release metadata has no unique build identity: {tag}")
    image_sha256 = str(matches[0].get("image_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", image_sha256):
        raise ValueError(f"existing release metadata has invalid image sha256: {tag}")
    return image_sha256


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"required release asset is missing: {path}")
    if path.stat().st_size == 0:
        raise RuntimeError(f"required release asset is empty: {path}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_asset_paths(item: dict[str, str], metadata_dir: Path) -> list[Path]:
    ova = Path(item["ova_path"])
    checksum = Path(item["checksum_path"])
    image_asset = item.get("image_asset") or Path(item["image_path"]).name
    image = Path(item.get("image_asset_path") or ova.with_name(image_asset))
    image_checksum = image.with_name(f"{image.name}.sha256")
    family = managed_release_family(item["release_tag"])
    if family is None:
        raise ValueError(f"unmanaged release tag: {item['release_tag']}")
    release_metadata = metadata_dir / family
    return [ova, checksum, image, image_checksum, *(release_metadata / name for name in METADATA_ASSET_NAMES)]


def path_is_within(path: Path, root: Path) -> bool:
    return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))


def validate_publish_item(item: dict[str, str], metadata_dir: Path) -> None:
    tag = str(item.get("release_tag", ""))
    key = managed_release_image_key(tag)
    if key is None:
        raise ValueError(f"unmanaged release tag: {tag}")
    image_sha = str(item.get("image_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", image_sha):
        raise ValueError(f"invalid image sha256: {image_sha}")
    if key[1] != image_sha[:12]:
        raise ValueError(f"release tag image sha does not match payload: {tag}")
    dist_root = Path("dist")
    for path in expected_asset_paths(item, metadata_dir):
        if not path_is_within(path, dist_root):
            raise ValueError(f"release asset path is outside dist: {path}")


def verify_release_assets(tag: str, expected_names: set[str]) -> None:
    names = release_asset_names(tag)
    missing = expected_names - names
    if missing:
        raise RuntimeError(f"release {tag} is missing asset(s): {', '.join(sorted(missing))}")


def delete_stale_assets(tag: str, keep_assets: set[str]) -> None:
    for asset in list_release_assets(tag):
        name = str(asset.get("name", ""))
        if name in keep_assets or not is_managed_asset_name(name):
            continue
        result = run(["gh", "release", "delete-asset", tag, name, "--yes"], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        print(f"deleted stale asset: {name}")


def publish_item(item: dict[str, str], metadata_dir: Path) -> bool:
    validate_publish_item(item, metadata_dir)
    tag = item["release_tag"]
    key = managed_release_image_key(tag)
    if key is None:
        raise ValueError(f"unmanaged release tag: {tag}")
    assets = expected_asset_paths(item, metadata_dir)
    ova, checksum, image, image_checksum = assets[:4]
    image_asset = item.get("image_asset") or Path(item["image_path"]).name
    for path in assets:
        require_file(path)
    if sha256_file(image) != item["image_sha256"]:
        raise ValueError(f"raw image sha256 does not match build result: {image}")
    checksum_parts = checksum.read_text(encoding="utf-8").split()
    if len(checksum_parts) != 2 or checksum_parts[1] != ova.name:
        raise ValueError(f"invalid OVA checksum file: {checksum}")
    if sha256_file(ova) != checksum_parts[0]:
        raise ValueError(f"OVA sha256 does not match checksum file: {ova}")
    image_checksum_parts = image_checksum.read_text(encoding="utf-8").split()
    if len(image_checksum_parts) != 2 or image_checksum_parts[1] != image.name:
        raise ValueError(f"invalid raw image checksum file: {image_checksum}")
    if image_checksum_parts[0] != item["image_sha256"]:
        raise ValueError(f"raw image checksum file does not match build result: {image_checksum}")
    for metadata_path in assets[4:]:
        json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_names = {path.name for path in assets}
    if image.name != image_asset:
        raise ValueError(f"raw image asset name mismatch: expected {image_asset}, got {image.name}")
    note_lines = [
        f"Image: `{image_asset}`",
        f"Image SHA256: `{item['image_sha256']}`",
        f"Builder version: `{item['builder_version']}`",
        "",
        "Supply-chain metadata and SPDX SBOM are attached to this release.",
    ]
    provenance = json.loads((metadata_dir / key[0] / "upstream-provenance.json").read_text(encoding="utf-8"))
    exceptions = sorted(
        {
            str(record.get("verification_status"))
            for record in provenance.get("records", [])
            if "untrusted" in str(record.get("verification_status", ""))
            or "unverified" in str(record.get("verification_status", ""))
        }
    )
    source_inventory = json.loads(
        (metadata_dir / key[0] / "source-inventory.json").read_text(encoding="utf-8")
    )
    source_refs = source_inventory.get("upstream_source_refs", {})
    for group_name in ("feeds", "components"):
        group = source_refs.get(group_name, {})
        if not isinstance(group, dict):
            continue
        exceptions.extend(
            str(record["artifact_source_relation"])
            for record in group.values()
            if isinstance(record, dict) and record.get("artifact_source_relation")
        )
    exceptions = sorted(set(exceptions))
    if exceptions:
        note_lines.extend(
            [
                "",
                "WARNING: this build includes upstream verification exceptions:",
                *(f"- `{status}`" for status in exceptions),
                "Review `upstream-provenance.json` before use.",
            ]
        )
    if item.get("release_date"):
        note_lines.insert(0, f"Build date: `{item['release_date']}`")
    if item.get("immortalwrt_version_code"):
        note_lines.insert(1, f"ImmortalWrt version: `{item['immortalwrt_version_code']}`")
    if item.get("immortalwrt_commit"):
        note_lines.insert(2, f"ImmortalWrt commit: `{item['immortalwrt_commit']}`")
    notes = "\n".join(note_lines)
    if release_exists(tag):
        existing_sha256 = existing_release_image_sha256(tag)
        if existing_sha256 is not None and existing_sha256 != item["image_sha256"]:
            raise RuntimeError(
                f"release tag collision for {tag}: "
                f"existing image {existing_sha256}, new image {item['image_sha256']}"
            )
        result = run(["gh", "release", "edit", tag, "--title", item["release_title"], "--notes", notes], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        result = run(
            ["gh", "release", "upload", tag, *(str(path) for path in assets), "--clobber"],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        delete_stale_assets(tag, expected_names)
        verify_release_assets(tag, expected_names)
        print(f"updated existing release: {tag}")
        return False

    command = [
        "gh",
        "release",
        "create",
        tag,
        *(str(path) for path in assets),
        "--title",
        item["release_title"],
        "--notes",
        notes,
    ]
    result = run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    verify_release_assets(tag, expected_names)
    print(f"published release: {tag}")
    return True


def list_releases(limit: int = 1000) -> list[dict[str, object]]:
    result = run(
        [
            "gh",
            "release",
            "list",
            "--limit",
            str(limit),
            "--json",
            "tagName,createdAt,isDraft,isPrerelease",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout or "[]")


def prune_old_releases(keep_releases: int) -> None:
    releases_by_family: dict[str, list[dict[str, object]]] = {}
    for release in list_releases():
        tag = str(release.get("tagName", ""))
        family = managed_release_family(tag)
        if family is None:
            continue
        releases_by_family.setdefault(family, []).append(release)

    for family, releases in sorted(releases_by_family.items()):
        releases.sort(key=lambda release: str(release.get("createdAt", "")), reverse=True)
        stale_releases = releases[keep_releases:]
        for release in stale_releases:
            tag = str(release["tagName"])
            result = run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            print(f"deleted old {family} release: {tag}")


def cleanup_old_releases(keep_releases: int) -> None:
    try:
        prune_old_releases(keep_releases)
    except Exception as exc:
        print(f"warning: release cleanup failed: {exc}", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--keep-releases", type=int, default=30, help="number of managed releases to keep")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if args.keep_releases < 1:
            raise ValueError("--keep-releases must be at least 1")
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        published_any = False
        for item in payload.get("built", []):
            published_any = publish_item(item, args.metadata_dir) or published_any
        if published_any:
            cleanup_old_releases(args.keep_releases)
        else:
            print("no new release published; skip cleanup")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
