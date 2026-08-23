#!/usr/bin/env python3
"""Preflight helpers for the OpenWrt ImageBuilder workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_USER_AGENT = "danbao-openwrt-builder"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
REQUIRED_IMAGEBUILDER_OPTIONS = (
    "CONFIG_SIGNED_PACKAGES",
    "CONFIG_SIGNATURE_CHECK",
    "CONFIG_DOWNLOAD_CHECK_CERTIFICATE",
    "CONFIG_JSON_OVERVIEW_IMAGE_INFO",
    "CONFIG_JSON_CYCLONEDX_SBOM",
)
PROFILE_REQUIRED_FIELDS = (
    "schema_version",
    "name",
    "profile",
    "rootfs_partsize",
    "nic_count",
    "image_glob",
    "packages",
    "required_packages",
    "forbidden_packages",
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_string_list(profile: dict, key: str) -> list[str]:
    values = profile.get(key)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"build profile {key} must be a list of non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"build profile {key} contains a duplicate package")
    return values


def load_build_profile(path: Path) -> dict:
    profile = json.loads(path.read_text(encoding="utf-8"))
    missing_fields = [key for key in PROFILE_REQUIRED_FIELDS if key not in profile]
    if missing_fields:
        raise ValueError(f"build profile is missing required field(s): {', '.join(missing_fields)}")
    if profile["schema_version"] != 1:
        raise ValueError(f"unsupported build profile schema_version: {profile['schema_version']!r}")
    for key in ("name", "profile", "image_glob"):
        if not isinstance(profile[key], str) or not profile[key].strip():
            raise ValueError(f"build profile {key} must be a non-empty string")
    for key in ("rootfs_partsize", "nic_count"):
        if not isinstance(profile[key], int) or isinstance(profile[key], bool) or profile[key] < 1:
            raise ValueError(f"build profile {key} must be a positive integer")

    packages = _validate_string_list(profile, "packages")
    required = _validate_string_list(profile, "required_packages")
    forbidden = _validate_string_list(profile, "forbidden_packages")
    missing_packages = sorted(set(required) - set(packages))
    if missing_packages:
        raise ValueError(f"build profile is missing required package(s): {', '.join(missing_packages)}")
    forbidden_packages = sorted(set(forbidden) & set(packages))
    if forbidden_packages:
        raise ValueError(f"build profile contains forbidden package(s): {', '.join(forbidden_packages)}")
    return profile


def validate_imagebuilder_config(payload: str) -> None:
    enabled = {
        match.group(1)
        for line in payload.splitlines()
        if (match := re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=y", line.strip()))
    }
    missing = [option for option in REQUIRED_IMAGEBUILDER_OPTIONS if option not in enabled]
    if missing:
        raise ValueError(f"ImageBuilder config is missing required option(s): {', '.join(missing)}")


def parse_package_manifest(payload: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in payload.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.+:-]+)\s+-\s+(.+)", line.strip())
        if match:
            packages[match.group(1)] = match.group(2).strip()
    if not packages:
        raise ValueError("package manifest contains no package entries")
    return packages


def validate_package_manifest(payload: str, profile: dict) -> dict[str, str]:
    packages = parse_package_manifest(payload)
    missing = sorted(set(profile["required_packages"]) - set(packages))
    if missing:
        raise ValueError(f"package manifest is missing required package(s): {', '.join(missing)}")
    forbidden = sorted(set(profile["forbidden_packages"]) & set(packages))
    if forbidden:
        raise ValueError(f"package manifest contains forbidden package(s): {', '.join(forbidden)}")
    return packages


def require_single_output(target_dir: Path, pattern: str, label: str) -> Path:
    candidates = sorted(path for path in target_dir.glob(pattern) if path.is_file())
    if not candidates:
        raise FileNotFoundError(f"required final image {label} is missing in {target_dir}")
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one final image {label} matching {pattern}, found {len(candidates)}"
        )
    output = candidates[0]
    if output.stat().st_size == 0:
        raise RuntimeError(f"required final image {label} is empty: {output}")
    return output


def collect_image_outputs(target_dir: Path, image_glob: str, out_dir: Path) -> dict[str, Path]:
    images = sorted(path for path in target_dir.glob(image_glob) if path.is_file())
    if len(images) != 1:
        raise ValueError(f"expected exactly one image matching {image_glob}, found {len(images)}")
    image = images[0]
    if not image.name.endswith(".img.gz"):
        raise ValueError(f"selected image does not end with .img.gz: {image}")
    manifest = require_single_output(target_dir, "*.manifest", "manifest")
    sbom = require_single_output(target_dir, "*.bom.cdx.json", "SBOM")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "image": out_dir / "immortalwrt-x86-64-daed.img.gz",
        "manifest": out_dir / "final-image.manifest",
        "sbom": out_dir / "final-image.bom.cdx.json",
    }
    shutil.copy2(image, outputs["image"])
    shutil.copy2(manifest, outputs["manifest"])
    shutil.copy2(sbom, outputs["sbom"])
    return outputs


def request_headers() -> dict[str, str]:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_bytes(
    url: str,
    *,
    timeout: int,
    retries: int,
    max_bytes: int = MAX_METADATA_BYTES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=request_headers())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > max_bytes:
                    raise RuntimeError(f"response exceeds size limit for {url}: {content_length} bytes")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise RuntimeError(f"response exceeds size limit for {url}: more than {max_bytes} bytes")
                return payload
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt == retries:
                break
            print(f"warning: request failed ({attempt}/{retries}) for {url}: {exc}", file=sys.stderr)
            time.sleep(min(5 * attempt, 30))
    raise RuntimeError(f"request failed for {url}: {last_error}")


def download_url(
    url: str,
    output: Path,
    *,
    timeout: int,
    retries: int,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    data = fetch_bytes(url, timeout=timeout, retries=retries, max_bytes=max_bytes)
    output.write_bytes(data)
    if output.stat().st_size == 0:
        raise RuntimeError(f"downloaded empty file: {url}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def imagebuilder_archive_name(version: str, target: str) -> str:
    target_name = target.strip("/").replace("/", "-")
    return f"immortalwrt-imagebuilder-{version}-{target_name}.Linux-x86_64.tar.zst"


def parse_sha256sums(payload: str, filename: str) -> str:
    matches = []
    for line in payload.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == filename:
            matches.append(parts[0])
    if len(matches) != 1:
        raise ValueError(f"expected one sha256 entry for {filename}, found {len(matches)}")
    return matches[0]


def write_env(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def github_api_json(url: str, *, timeout: int, retries: int) -> dict:
    return json.loads(fetch_bytes(url, timeout=timeout, retries=retries).decode("utf-8"))


def release_api_url(repo: str, tag: str | None) -> str:
    base = f"https://api.github.com/repos/{repo}/releases"
    if tag and tag != "latest":
        return f"{base}/tags/{tag}"
    return f"{base}/latest"


def cmd_imagebuilder_info(args: argparse.Namespace) -> int:
    base_url = f"https://downloads.immortalwrt.org/releases/{args.version}/targets/{args.target.strip('/')}"
    archive = imagebuilder_archive_name(args.version, args.target)
    sha_payload = fetch_bytes(f"{base_url}/sha256sums", timeout=args.timeout, retries=args.retries).decode("utf-8")
    archive_sha = parse_sha256sums(sha_payload, archive)
    values = {
        "IB_TARGET": args.target.strip("/"),
        "IB_BASE_URL": base_url,
        "IB_ARCHIVE": archive,
        "IB_ARCHIVE_SHA256": archive_sha,
    }
    if args.github_env:
        write_env(args.github_env, values)
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


def cmd_download_url(args: argparse.Namespace) -> int:
    download_url(args.url, args.output, timeout=args.timeout, retries=args.retries)
    if args.sha256:
        actual = sha256_file(args.output)
        if actual != args.sha256:
            raise ValueError(f"sha256 mismatch for {args.output}: expected {args.sha256}, got {actual}")
    print(f"downloaded: {args.output}")
    return 0


def cmd_validate_profile(args: argparse.Namespace) -> int:
    profile = load_build_profile(args.config)
    values = {
        "BUILD_PROFILE": profile["profile"],
        "ROOTFS_PARTSIZE": str(profile["rootfs_partsize"]),
        "NIC_COUNT": str(profile["nic_count"]),
        "IMAGE_GLOB": profile["image_glob"],
        "BUILD_PACKAGES": " ".join(profile["packages"]),
    }
    if args.github_env:
        write_env(args.github_env, values)
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


def cmd_validate_imagebuilder(args: argparse.Namespace) -> int:
    load_build_profile(args.profile)
    validate_imagebuilder_config(args.config.read_text(encoding="utf-8"))
    print(f"validated ImageBuilder security configuration: {args.config}")
    return 0


def cmd_validate_manifest(args: argparse.Namespace) -> int:
    profile = load_build_profile(args.profile)
    packages = validate_package_manifest(args.manifest.read_text(encoding="utf-8"), profile)
    official_packages = {
        package: packages[package]
        for package in profile["required_packages"]
        if package == "daed" or package.startswith("daed-") or package.startswith("luci-app-daed") or package.startswith("luci-i18n-daed")
    }
    write_json(
        args.metadata_out,
        {
            "schema_version": 1,
            "source": "official-immortalwrt",
            "packages": official_packages,
        },
    )
    print(f"validated official package manifest: {args.manifest}")
    return 0


def cmd_collect_image_outputs(args: argparse.Namespace) -> int:
    outputs = collect_image_outputs(args.target_dir, args.image_glob, args.out_dir)
    for name, path in outputs.items():
        print(f"{name}={path}")
    return 0


def cmd_verify_records(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    doc_text = args.doc.read_text(encoding="utf-8")
    tags = {str(item.get("release_tag", "")) for item in manifest.get("conversions", {}).values()}

    expected_tags = set(args.release_tag or [])
    if args.check_latest_release:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            raise ValueError("--repo or GITHUB_REPOSITORY is required for --check-latest-release")
        release = github_api_json(release_api_url(repo, "latest"), timeout=args.timeout, retries=args.retries)
        expected_tags.add(str(release.get("tag_name", "")))

    for tag in sorted(expected_tags):
        if not tag:
            continue
        if tag not in tags:
            raise RuntimeError(f"release tag missing from manifest: {tag}")
        if tag not in doc_text:
            raise RuntimeError(f"release tag missing from docs: {tag}")
        print(f"verified release record: {tag}")
    return 0


def add_common_network_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=5)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    imagebuilder_info = subparsers.add_parser("imagebuilder-info", help="resolve and verify ImageBuilder metadata")
    imagebuilder_info.add_argument("--version", required=True)
    imagebuilder_info.add_argument("--target", default="x86/64")
    imagebuilder_info.add_argument("--github-env", type=Path)
    add_common_network_args(imagebuilder_info)
    imagebuilder_info.set_defaults(func=cmd_imagebuilder_info)

    download = subparsers.add_parser("download-url", help="download a URL with retries and optional sha256 check")
    download.add_argument("--url", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--sha256")
    add_common_network_args(download)
    download.set_defaults(func=cmd_download_url)

    validate_profile = subparsers.add_parser("validate-profile", help="validate and export the build profile")
    validate_profile.add_argument("--config", type=Path, required=True)
    validate_profile.add_argument("--github-env", type=Path)
    validate_profile.set_defaults(func=cmd_validate_profile)

    validate_imagebuilder = subparsers.add_parser(
        "validate-imagebuilder", help="validate ImageBuilder supply-chain security options"
    )
    validate_imagebuilder.add_argument("--profile", type=Path, required=True)
    validate_imagebuilder.add_argument("--config", type=Path, required=True)
    validate_imagebuilder.set_defaults(func=cmd_validate_imagebuilder)

    validate_manifest = subparsers.add_parser(
        "validate-manifest", help="validate official package availability and record versions"
    )
    validate_manifest.add_argument("--profile", type=Path, required=True)
    validate_manifest.add_argument("--manifest", type=Path, required=True)
    validate_manifest.add_argument("--metadata-out", type=Path, required=True)
    validate_manifest.set_defaults(func=cmd_validate_manifest)

    collect_outputs = subparsers.add_parser(
        "collect-image-outputs", help="select exactly one image and copy its required audit sidecars"
    )
    collect_outputs.add_argument("--target-dir", type=Path, required=True)
    collect_outputs.add_argument("--image-glob", required=True)
    collect_outputs.add_argument("--out-dir", type=Path, required=True)
    collect_outputs.set_defaults(func=cmd_collect_image_outputs)

    verify_records = subparsers.add_parser("verify-records", help="verify manifest/docs contain expected release tags")
    verify_records.add_argument("--manifest", type=Path, required=True)
    verify_records.add_argument("--doc", type=Path, required=True)
    verify_records.add_argument("--release-tag", action="append")
    verify_records.add_argument("--check-latest-release", action="store_true")
    verify_records.add_argument("--repo")
    add_common_network_args(verify_records)
    verify_records.set_defaults(func=cmd_verify_records)

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
