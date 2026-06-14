#!/usr/bin/env python3
"""Publish built OVA files to GitHub Releases."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def release_exists(tag: str) -> bool:
    result = run(["gh", "release", "view", tag], check=False)
    return result.returncode == 0


def publish_item(item: dict[str, str]) -> None:
    tag = item["release_tag"]
    if release_exists(tag):
        print(f"release already exists: {tag}")
        return

    ova = Path(item["ova_path"])
    checksum = Path(item["checksum_path"])
    notes = "\n".join(
        [
            f"Image: `{Path(item['image_path']).name}`",
            f"Image SHA256: `{item['image_sha256']}`",
            f"Builder version: `{item['builder_version']}`",
        ]
    )
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        for item in payload.get("built", []):
            publish_item(item)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
