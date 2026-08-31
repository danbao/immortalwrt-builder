#!/usr/bin/env python3
"""Publish built OVA files to GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

RELEASE_TAG_FAMILY_PATTERNS = (
    ("bypass", re.compile(r"^openwrt-immortalwrt-x86-64-bypass-(?:[0-9a-f]{12}|\d{8}-[0-9a-f]+-[0-9a-f]{12})$")),
    ("daed", re.compile(r"^openwrt-immortalwrt-x86-64-daed-(?:[0-9a-f]{12}|\d{8}-[0-9a-f]+-[0-9a-f]{12})$")),
    ("standard", re.compile(r"^openwrt-immortalwrt-x86-64-(?:[0-9a-f]{12}|\d{8}-[0-9a-f]+-[0-9a-f]{12})$")),
)
# Longest first so a more specific family never resolves to a shorter prefix.
RELEASE_ASSET_FAMILY_PREFIXES = (
    "immortalwrt-x86-64-bypass",
    "immortalwrt-x86-64-daed",
    "immortalwrt-x86-64",
)
ASSET_NAME_PATTERN = re.compile(
    r"^(?:immortalwrt-x86-64.*(?:\.img\.gz|\.ova|\.ova\.sha256|\.manifest|\.bom\.cdx\.json)|"
    r"SHA256SUMS|build-metadata\.json|build-metadata\.tar\.gz|setup-openwrt\.sh)$"
)
REQUIRED_RUNTIME_PACKAGES = {
    "luci",
    "luci-i18n-base-zh-cn",
    "luci-theme-argon",
    "luci-app-vlmcsd",
    "luci-i18n-firewall-zh-cn",
    "luci-i18n-package-manager-zh-cn",
    "daed",
    "daed-geoip",
    "daed-geosite",
    "luci-app-daed",
    "luci-i18n-daed-zh-cn",
    "tailscale",
    "luci-app-tailscale-community",
    "luci-i18n-tailscale-community-zh-cn",
    "vnstat2",
    "vnstati2",
    "luci-app-vnstat2",
    "luci-i18n-vnstat2-zh-cn",
    "sqlite3-cli",
    "open-vm-tools",
}


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


def asset_family_prefix(artifact_name: str) -> str:
    for prefix in RELEASE_ASSET_FAMILY_PREFIXES:
        if artifact_name.startswith(f"{prefix}-"):
            return prefix
    raise ValueError(f"artifact name does not belong to a managed release family: {artifact_name!r}")


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


def release_asset_paths(item: dict[str, object], asset_root: Path) -> list[Path]:
    values = item.get("release_assets")
    if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
        raise ValueError("built item release_assets must be a non-empty list of paths")
    trusted_root = asset_root.resolve()
    paths = [Path(value).resolve() for value in values]
    outside = [path for path in paths if not path.is_relative_to(trusted_root)]
    if outside:
        raise ValueError(f"release asset is outside trusted asset directory: {outside[0]}")
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise ValueError("built item release_assets contains duplicate asset names")
    return paths


def parse_checksum_lines(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError(f"invalid checksum line {lineno} in {path.name}")
        name = parts[1].lstrip("* ")
        if not name or Path(name).name != name or name in checksums:
            raise ValueError(f"invalid or duplicate checksum asset on line {lineno} in {path.name}")
        checksums[name] = parts[0]
    return checksums


def validate_release_payload(
    item: dict[str, object],
    asset_root: Path,
    *,
    expected_repository_commit: str | None = None,
    expected_workflow_run_url: str | None = None,
) -> list[Path]:
    assets = release_asset_paths(item, asset_root)
    for path in assets:
        require_file(path)
    by_name = {path.name: path for path in assets}
    tag = str(item.get("release_tag") or "")
    if not is_managed_release_tag(tag):
        raise ValueError(f"release tag is not a managed release tag: {tag!r}")
    image_asset = str(item.get("image_asset") or "")
    if not image_asset.endswith(".img.gz"):
        raise ValueError(f"invalid image_asset: {image_asset!r}")
    expected_tag = f"openwrt-{image_asset.removesuffix('.img.gz')}"
    if tag != expected_tag:
        raise ValueError(f"release tag does not match image asset: expected {expected_tag}, got {tag}")

    artifact_name = image_asset.removesuffix(".img.gz")
    family_prefix = asset_family_prefix(artifact_name)
    artifact_suffix = artifact_name.removeprefix(f"{family_prefix}-")
    expected_ova_name = f"{family_prefix}-esxi-{artifact_suffix}.ova"
    expected_ova_checksum_name = f"{expected_ova_name}.sha256"
    if Path(str(item.get("ova_path") or "")).name != expected_ova_name:
        raise ValueError(f"OVA asset name mismatch: expected {expected_ova_name}")
    if Path(str(item.get("checksum_path") or "")).name != expected_ova_checksum_name:
        raise ValueError(f"OVA checksum asset name mismatch: expected {expected_ova_checksum_name}")
    expected_names = {
        image_asset,
        expected_ova_name,
        expected_ova_checksum_name,
        f"{artifact_name}.manifest",
        f"{artifact_name}.bom.cdx.json",
        "build-metadata.json",
        "build-metadata.tar.gz",
        "setup-openwrt.sh",
        "SHA256SUMS",
    }
    if set(by_name) != expected_names:
        missing = sorted(expected_names - set(by_name))
        extra = sorted(set(by_name) - expected_names)
        raise ValueError(f"release asset set mismatch; missing={missing}, extra={extra}")

    image_sha = sha256_file(by_name[image_asset])
    if image_sha != item.get("image_sha256"):
        raise ValueError(f"raw image SHA256 mismatch: expected {item.get('image_sha256')}, got {image_sha}")
    if not artifact_name.endswith(image_sha[:12]):
        raise ValueError("raw image asset name does not contain its SHA256 prefix")

    ova_name = Path(str(item["ova_path"])).name
    ova_checksums = parse_checksum_lines(by_name[Path(str(item["checksum_path"])).name])
    if ova_checksums != {ova_name: sha256_file(by_name[ova_name])}:
        raise ValueError("OVA SHA256 mismatch")

    sums = parse_checksum_lines(by_name["SHA256SUMS"])
    checksum_assets = set(by_name) - {"SHA256SUMS"}
    if set(sums) != checksum_assets:
        raise ValueError("SHA256SUMS asset set mismatch")
    for name in sorted(checksum_assets):
        actual = sha256_file(by_name[name])
        if sums[name] != actual:
            raise ValueError(f"SHA256 mismatch for release asset {name}: expected {sums[name]}, got {actual}")

    metadata = json.loads(by_name["build-metadata.json"].read_text(encoding="utf-8"))
    if metadata != item.get("build_metadata"):
        raise ValueError("build metadata file does not match build-results.json")
    if metadata.get("source") != "official-immortalwrt":
        raise ValueError("build metadata source must be official-immortalwrt")
    metadata_profile = metadata.get("profile", {})
    declared_required = metadata_profile.get("required_packages", []) if isinstance(metadata_profile, dict) else []
    if set(declared_required) != REQUIRED_RUNTIME_PACKAGES:
        raise ValueError("build metadata required package baseline does not match the trusted runtime baseline")
    if not REQUIRED_RUNTIME_PACKAGES <= set(metadata.get("packages", {})):
        raise ValueError("build metadata is missing required official package versions")
    provenance = metadata.get("provenance", {})
    for field, expected in (
        ("repository_commit", expected_repository_commit),
        ("workflow_run_url", expected_workflow_run_url),
    ):
        if expected is not None and provenance.get(field) != expected:
            raise ValueError(f"build metadata {field} does not match trusted publish context")
    return assets


def verify_release_assets(tag: str, expected_assets: list[Path]) -> None:
    remote_assets = {str(asset.get("name", "")): asset for asset in list_release_assets(tag)}
    expected_names = {path.name for path in expected_assets}
    missing = expected_names - set(remote_assets)
    if missing:
        raise RuntimeError(f"release {tag} is missing asset(s): {', '.join(sorted(missing))}")
    for path in expected_assets:
        expected_digest = f"sha256:{sha256_file(path)}"
        remote_digest = str(remote_assets[path.name].get("digest") or "")
        if remote_digest != expected_digest:
            raise RuntimeError(
                f"release {tag} asset digest mismatch for {path.name}: "
                f"expected {expected_digest}, got {remote_digest or 'missing'}"
            )


def delete_stale_assets(tag: str, keep_assets: set[str]) -> None:
    for asset in list_release_assets(tag):
        name = str(asset.get("name", ""))
        if name in keep_assets or not is_managed_asset_name(name):
            continue
        result = run(["gh", "release", "delete-asset", tag, name, "--yes"], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        print(f"deleted stale asset: {name}")


def release_notes(item: dict[str, object]) -> str:
    metadata = item.get("build_metadata") or {}
    profile = metadata.get("profile", {}) if isinstance(metadata, dict) else {}
    packages = metadata.get("packages", {}) if isinstance(metadata, dict) else {}
    provenance = metadata.get("provenance", {}) if isinstance(metadata, dict) else {}
    note_lines = [
        f"Image SHA256: `{item['image_sha256']}`",
        f"Builder version: `{item['builder_version']}`",
        f"Package source: `{metadata.get('source', 'unknown')}`",
    ]
    for package, version in sorted(packages.items()):
        note_lines.append(f"{package}: `{version}`")
    if profile:
        note_lines.append(
            "Profile: `{profile}`, rootfs: `{rootfs}` MiB, NICs: `{nics}`".format(
                profile=profile.get("imagebuilder_profile", "unknown"),
                rootfs=profile.get("rootfs_partsize", "unknown"),
                nics=profile.get("nic_count", "unknown"),
            )
        )
    if item.get("release_date"):
        note_lines.insert(0, f"Build date: `{item['release_date']}`")
    if item.get("immortalwrt_version_code"):
        note_lines.append(f"ImmortalWrt version: `{item['immortalwrt_version_code']}`")
    if item.get("immortalwrt_commit"):
        note_lines.append(f"ImmortalWrt commit: `{item['immortalwrt_commit']}`")
    if provenance.get("workflow_run_url"):
        note_lines.append(f"Workflow run: {provenance['workflow_run_url']}")
    return "\n".join(note_lines)


def publish_item(
    item: dict[str, object],
    asset_root: Path,
    *,
    expected_repository_commit: str | None = None,
    expected_workflow_run_url: str | None = None,
) -> bool:
    tag = item["release_tag"]
    assets = validate_release_payload(
        item,
        asset_root,
        expected_repository_commit=expected_repository_commit,
        expected_workflow_run_url=expected_workflow_run_url,
    )
    expected_names = {path.name for path in assets}
    notes = release_notes(item)
    if release_exists(tag):
        verify_release_assets(str(tag), assets)
        delete_stale_assets(tag, expected_names)
        print(f"verified existing immutable release: {tag}")
        return False

    command = [
        "gh",
        "release",
        "create",
        str(tag),
        *(str(path) for path in assets),
        "--title",
        item["release_title"],
        "--notes",
        notes,
    ]
    result = run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    verify_release_assets(str(tag), assets)
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
    parser.add_argument("--keep-releases", type=int, default=30, help="number of managed releases to keep")
    parser.add_argument("--expected-repository-commit", required=True)
    parser.add_argument("--expected-workflow-run-url", required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if args.keep_releases < 1:
            raise ValueError("--keep-releases must be at least 1")
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        published_any = False
        for item in payload.get("built", []):
            published_any = (
                publish_item(
                    item,
                    args.results.parent,
                    expected_repository_commit=args.expected_repository_commit,
                    expected_workflow_run_url=args.expected_workflow_run_url,
                )
                or published_any
            )
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
