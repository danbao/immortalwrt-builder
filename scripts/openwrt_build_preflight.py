#!/usr/bin/env python3
"""Preflight helpers for the OpenWrt ImageBuilder workflow."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_USER_AGENT = "danbao-openwrt-builder"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    required: bool


def strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def read_packages(path: Path) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        clean = strip_comment(line)
        if not clean:
            continue
        for package in clean.split():
            if package in seen:
                raise ValueError(f"duplicate package in {path}:{lineno}: {package}")
            seen.add(package)
            packages.append(package)
    if not packages:
        raise ValueError(f"package list is empty: {path}")
    return packages


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "required"}:
        return True
    if normalized in {"0", "false", "no", "optional"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def read_feeds(path: Path) -> list[Feed]:
    feeds: list[Feed] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        clean = strip_comment(line)
        if not clean:
            continue
        parts = clean.split("\t")
        if len(parts) != 3:
            raise ValueError(f"expected 3 tab-separated fields in {path}:{lineno}")
        name, url, required = (part.strip() for part in parts)
        if not name or not url:
            raise ValueError(f"feed name/url cannot be empty in {path}:{lineno}")
        if name in seen:
            raise ValueError(f"duplicate feed in {path}:{lineno}: {name}")
        seen.add(name)
        feeds.append(Feed(name=name, url=url.rstrip("/"), required=parse_bool(required)))
    if not feeds:
        raise ValueError(f"feed list is empty: {path}")
    return feeds


def request_headers() -> dict[str, str]:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_bytes(url: str, *, timeout: int, retries: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=request_headers())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt == retries:
                break
            print(f"warning: request failed ({attempt}/{retries}) for {url}: {exc}", file=sys.stderr)
            time.sleep(min(5 * attempt, 30))
    raise RuntimeError(f"request failed for {url}: {last_error}")


def download_url(url: str, output: Path, *, timeout: int, retries: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    data = fetch_bytes(url, timeout=timeout, retries=retries)
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


def select_release_asset(assets: list[dict], pattern: str) -> dict:
    matches = [asset for asset in assets if fnmatch.fnmatch(str(asset.get("name", "")), pattern)]
    if len(matches) != 1:
        names = ", ".join(str(asset.get("name", "")) for asset in matches) or "none"
        raise ValueError(f"expected one release asset matching {pattern}, found {len(matches)}: {names}")
    return matches[0]


def append_feeds(feed_file: Path, repositories_conf: Path) -> None:
    feeds = read_feeds(feed_file)
    repositories_conf.parent.mkdir(parents=True, exist_ok=True)
    existing = repositories_conf.read_text(encoding="utf-8") if repositories_conf.exists() else ""
    lines = [line for line in existing.splitlines() if "option check_signature" not in line]
    for feed in feeds:
        prefix = f"src/gz {feed.name} "
        if not any(line.startswith(prefix) for line in lines):
            lines.append(f"src/gz {feed.name} {feed.url}")
    repositories_conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Configured repositories:")
    for line in lines:
        if line.startswith("src/gz "):
            print(line)


def check_feed(feed: Feed, *, timeout: int, retries: int) -> bool:
    url = f"{feed.url}/Packages.gz"
    print(f"Probing feed {feed.name}: {url}")
    try:
        fetch_bytes(url, timeout=timeout, retries=retries)
    except Exception as exc:
        message = f"feed unreachable: {feed.name} ({url}): {exc}"
        if feed.required:
            print(f"::error::{message}", file=sys.stderr)
            return False
        print(f"warning: optional {message}", file=sys.stderr)
        return True
    print(f"  ok {feed.name} reachable")
    return True


def cmd_package_string(args: argparse.Namespace) -> int:
    print(" ".join(read_packages(args.package_file)))
    return 0


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


def cmd_append_feeds(args: argparse.Namespace) -> int:
    append_feeds(args.feed_file, args.repositories_conf)
    return 0


def cmd_check_feeds(args: argparse.Namespace) -> int:
    feeds = read_feeds(args.feed_file)
    results = [check_feed(feed, timeout=args.timeout, retries=args.retries) for feed in feeds]
    if not all(results):
        raise RuntimeError("one or more required feeds are unreachable")
    return 0


def cmd_download_release_asset(args: argparse.Namespace) -> int:
    release = github_api_json(release_api_url(args.repo, args.tag), timeout=args.timeout, retries=args.retries)
    asset = select_release_asset(release.get("assets", []), args.pattern)
    url = str(asset.get("browser_download_url") or "")
    if not url:
        raise ValueError(f"release asset has no browser_download_url: {asset.get('name')}")
    output = args.dir / str(asset["name"])
    download_url(url, output, timeout=args.timeout, retries=args.retries)
    print(f"downloaded release asset: {args.repo}@{release.get('tag_name')} {output}")
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

    package_string = subparsers.add_parser("package-string", help="print packages as a single ImageBuilder string")
    package_string.add_argument("--package-file", type=Path, required=True)
    package_string.set_defaults(func=cmd_package_string)

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

    append = subparsers.add_parser("append-feeds", help="append configured feeds to repositories.conf")
    append.add_argument("--feed-file", type=Path, required=True)
    append.add_argument("--repositories-conf", type=Path, required=True)
    append.set_defaults(func=cmd_append_feeds)

    check_feeds = subparsers.add_parser("check-feeds", help="probe configured feed Packages.gz files")
    check_feeds.add_argument("--feed-file", type=Path, required=True)
    add_common_network_args(check_feeds)
    check_feeds.set_defaults(func=cmd_check_feeds)

    download_asset = subparsers.add_parser("download-release-asset", help="download exactly one GitHub Release asset")
    download_asset.add_argument("--repo", required=True)
    download_asset.add_argument("--tag", default="latest")
    download_asset.add_argument("--pattern", required=True)
    download_asset.add_argument("--dir", type=Path, required=True)
    add_common_network_args(download_asset)
    download_asset.set_defaults(func=cmd_download_release_asset)

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
