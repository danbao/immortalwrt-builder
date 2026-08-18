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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_daed_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("base_url", "sdk", "arch"):
        if not config.get(key):
            raise ValueError(f"daed feed config is missing required key: {key}")
    if not isinstance(config.get("packages"), list) or not config["packages"]:
        raise ValueError("daed feed config packages must be a non-empty list")
    config["base_url"] = str(config["base_url"]).rstrip("/")
    return config


def parse_daed_manifest(payload: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for lineno, line in enumerate(payload.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            raise ValueError(f"invalid daed manifest line {lineno}: {line!r}")
        if name.endswith("_sha256"):
            entries.setdefault(name[: -len("_sha256")], {})["sha256"] = value
        else:
            entries.setdefault(name, {})["filename"] = value
    for package, fields in entries.items():
        if not fields.get("filename"):
            raise ValueError(f"daed manifest is missing a filename entry for {package}")
    return entries


def extract_package_version(filename: str, package: str) -> str:
    prefix = f"{package}-"
    if not filename.endswith(".apk") or not filename.startswith(prefix):
        raise ValueError(f"daed manifest filename {filename!r} does not match package {package}")
    return filename[len(prefix) : -len(".apk")]


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


def cmd_daed_packages(args: argparse.Namespace) -> int:
    config = load_daed_config(args.config)
    feed_dir = f"{config['base_url']}/{config['sdk']}/{config['arch']}"
    manifest_url = f"{feed_dir}/manifest-daede.txt"
    manifest = parse_daed_manifest(
        fetch_bytes(
            manifest_url,
            timeout=args.timeout,
            retries=args.retries,
            max_bytes=MAX_METADATA_BYTES,
        ).decode("utf-8")
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    recorded: dict[str, dict[str, str]] = {}
    for package in config["packages"]:
        fields = manifest.get(package)
        if fields is None:
            raise ValueError(f"daed manifest has no entry for required package: {package}")
        expected_sha = fields.get("sha256") or ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError(f"daed manifest has invalid sha256 for {package}: {expected_sha!r}")
        filename = str((config.get("pin") or {}).get(package) or fields["filename"])
        url = f"{feed_dir}/{filename}"
        output = args.out_dir / filename
        download_url(
            url,
            output,
            timeout=args.timeout,
            retries=args.retries,
            max_bytes=MAX_DOWNLOAD_BYTES,
        )
        actual = sha256_file(output)
        if actual != expected_sha:
            raise ValueError(f"sha256 mismatch for {filename}: expected {expected_sha}, got {actual}")
        recorded[package] = {
            "filename": filename,
            "version": extract_package_version(filename, package),
            "sha256": actual,
            "url": url,
        }
        print(f"fetched daed package: {package} {filename} sha256={actual}")

    write_json(
        args.metadata_out,
        {
            "feed": config["base_url"],
            "sdk": config["sdk"],
            "arch": config["arch"],
            "packages": recorded,
        },
    )
    print(f"recorded daed package metadata: {args.metadata_out}")
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


def resolve_built_image(path_value: str, source_dir: Path) -> Path:
    image = Path(path_value)
    candidates = [image]
    if not image.is_absolute():
        candidates.append(source_dir / image.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"built image not found: {path_value}")


def cmd_copy_raw_images(args: argparse.Namespace) -> int:
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in payload.get("built", []):
        image_asset = item.get("image_asset")
        if not image_asset:
            raise ValueError(f"built item has no image_asset: {item}")
        source = resolve_built_image(str(item["image_path"]), args.source_dir)
        target = args.out_dir / str(image_asset)
        shutil.copy2(source, target)
        if target.stat().st_size == 0:
            raise RuntimeError(f"copied empty raw image asset: {target}")
        copied += 1
        print(f"copied raw image asset: {source} -> {target}")
    print(f"copied {copied} raw image asset(s)")
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

    daed_packages = subparsers.add_parser(
        "daed-packages",
        help="download sha256-verified daed apk packages from the kenzok8 feed into the ImageBuilder local package dir",
    )
    daed_packages.add_argument("--config", type=Path, required=True)
    daed_packages.add_argument("--out-dir", type=Path, required=True)
    daed_packages.add_argument("--metadata-out", type=Path, required=True)
    add_common_network_args(daed_packages)
    daed_packages.set_defaults(func=cmd_daed_packages)

    verify_records = subparsers.add_parser("verify-records", help="verify manifest/docs contain expected release tags")
    verify_records.add_argument("--manifest", type=Path, required=True)
    verify_records.add_argument("--doc", type=Path, required=True)
    verify_records.add_argument("--release-tag", action="append")
    verify_records.add_argument("--check-latest-release", action="store_true")
    verify_records.add_argument("--repo")
    add_common_network_args(verify_records)
    verify_records.set_defaults(func=cmd_verify_records)

    copy_raw_images = subparsers.add_parser("copy-raw-images", help="copy built raw images to their release asset names")
    copy_raw_images.add_argument("--results", type=Path, required=True)
    copy_raw_images.add_argument("--source-dir", type=Path, required=True)
    copy_raw_images.add_argument("--out-dir", type=Path, required=True)
    copy_raw_images.set_defaults(func=cmd_copy_raw_images)

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
