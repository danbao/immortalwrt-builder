#!/usr/bin/env python3
"""Publish built OVA files to GitHub Releases."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RELEASE_TAG_PATTERNS = (
    re.compile(r"^openwrt-immortalwrt-x86-64-[0-9a-f]{12}$"),
    re.compile(r"^openwrt-immortalwrt-x86-64-\d{8}-[0-9a-f]+-[0-9a-f]{12}$"),
)
ASSET_NAME_PATTERN = re.compile(r"^immortalwrt-x86-64.*(\.img\.gz|\.ova|\.ova\.sha256)$")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def release_exists(tag: str) -> bool:
    result = run(["gh", "release", "view", tag], check=False)
    return result.returncode == 0


def is_managed_release_tag(tag: str) -> bool:
    return any(pattern.fullmatch(tag) for pattern in RELEASE_TAG_PATTERNS)


def is_managed_asset_name(name: str) -> bool:
    return bool(ASSET_NAME_PATTERN.fullmatch(name))


def list_release_assets(tag: str) -> list[dict[str, object]]:
    result = run(["gh", "release", "view", tag, "--json", "assets"], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    payload = json.loads(result.stdout or "{}")
    return payload.get("assets", [])


def delete_stale_assets(tag: str, keep_assets: set[str]) -> None:
    for asset in list_release_assets(tag):
        name = str(asset.get("name", ""))
        if name in keep_assets or not is_managed_asset_name(name):
            continue
        result = run(["gh", "release", "delete-asset", tag, name, "--yes"], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        print(f"deleted stale asset: {name}")


def publish_item(item: dict[str, str]) -> bool:
    tag = item["release_tag"]
    ova = Path(item["ova_path"])
    checksum = Path(item["checksum_path"])
    image_asset = item.get("image_asset") or Path(item["image_path"]).name
    note_lines = [
        f"Image: `{image_asset}`",
        f"Image SHA256: `{item['image_sha256']}`",
        f"Builder version: `{item['builder_version']}`",
    ]
    if item.get("release_date"):
        note_lines.insert(0, f"Build date: `{item['release_date']}`")
    if item.get("immortalwrt_version_code"):
        note_lines.insert(1, f"ImmortalWrt version: `{item['immortalwrt_version_code']}`")
    if item.get("immortalwrt_commit"):
        note_lines.insert(2, f"ImmortalWrt commit: `{item['immortalwrt_commit']}`")
    notes = "\n".join(note_lines)
    if release_exists(tag):
        result = run(["gh", "release", "edit", tag, "--title", item["release_title"], "--notes", notes], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        result = run(["gh", "release", "upload", tag, str(ova), str(checksum), "--clobber"], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        delete_stale_assets(tag, {ova.name, checksum.name, image_asset})
        print(f"updated existing release: {tag}")
        return False

    command = [
        "gh",
        "release",
        "create",
        tag,
        str(ova),
        str(checksum),
        "--title",
        item["release_title"],
        "--notes",
        notes,
    ]
    result = run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
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
    managed_releases = [
        release
        for release in list_releases()
        if is_managed_release_tag(str(release.get("tagName", "")))
    ]
    managed_releases.sort(key=lambda release: str(release.get("createdAt", "")), reverse=True)
    stale_releases = managed_releases[keep_releases:]
    for release in stale_releases:
        tag = str(release["tagName"])
        result = run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        print(f"deleted old release: {tag}")


def cleanup_old_releases(keep_releases: int) -> None:
    try:
        prune_old_releases(keep_releases)
    except Exception as exc:
        print(f"warning: release cleanup failed: {exc}", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
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
            published_any = publish_item(item) or published_any
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
