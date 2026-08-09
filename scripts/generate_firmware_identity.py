#!/usr/bin/env python3
"""Generate deterministic firmware identity metadata for an image flavor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_identity(
    *,
    flavor: str,
    target: str,
    builder_commit: str,
    imagebuilder_version: str,
    imagebuilder_sha256: str,
    immortalwrt_version_code: str,
    immortalwrt_commit: str,
    package_manifest: Path,
    provenance: Path,
    source_refs: Path,
) -> dict[str, object]:
    if flavor not in {"standard", "daed"}:
        raise ValueError(f"unsupported flavor: {flavor}")
    if target != "x86/64":
        raise ValueError(f"unsupported target: {target}")
    if not HEX_40.fullmatch(builder_commit):
        raise ValueError("builder commit must be a full lowercase SHA1")
    if not HEX_64.fullmatch(imagebuilder_sha256):
        raise ValueError("ImageBuilder SHA256 is invalid")
    for path in (package_manifest, provenance, source_refs):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"identity input is missing or empty: {path}")

    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "danbao/immortalwrt-builder",
        "flavor": flavor,
        "target": target,
        "builder_commit": builder_commit,
        "imagebuilder": {
            "version": imagebuilder_version,
            "sha256": imagebuilder_sha256,
        },
        "immortalwrt": {
            "version_code": immortalwrt_version_code,
            "commit": immortalwrt_commit,
        },
        "inputs": {
            "package_manifest_sha256": sha256_file(package_manifest),
            "upstream_provenance_sha256": sha256_file(provenance),
            "source_refs_sha256": sha256_file(source_refs),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["identity_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--builder-commit", required=True)
    parser.add_argument("--imagebuilder-version", required=True)
    parser.add_argument("--imagebuilder-sha256", required=True)
    parser.add_argument("--immortalwrt-version-code", required=True)
    parser.add_argument("--immortalwrt-commit", required=True)
    parser.add_argument("--package-manifest", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--source-refs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        payload = build_identity(
            flavor=args.flavor,
            target=args.target,
            builder_commit=args.builder_commit,
            imagebuilder_version=args.imagebuilder_version,
            imagebuilder_sha256=args.imagebuilder_sha256,
            immortalwrt_version_code=args.immortalwrt_version_code,
            immortalwrt_commit=args.immortalwrt_commit,
            package_manifest=args.package_manifest,
            provenance=args.provenance,
            source_refs=args.source_refs,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
