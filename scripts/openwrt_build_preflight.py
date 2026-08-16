#!/usr/bin/env python3
"""Preflight helpers for the OpenWrt ImageBuilder workflow."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_USER_AGENT = "danbao-openwrt-builder"
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024


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
    accept: str | None = None,
    max_bytes: int = MAX_METADATA_BYTES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        headers = request_headers()
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, headers=headers)
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
    accept: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        headers = request_headers()
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response, output.open("wb") as handle:
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > max_bytes:
                    raise RuntimeError(f"download exceeds size limit for {url}: {content_length} bytes")
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise RuntimeError(f"download exceeds size limit for {url}: more than {max_bytes} bytes")
                    handle.write(chunk)
            if output.stat().st_size == 0:
                raise RuntimeError(f"downloaded empty file: {url}")
            return
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            last_error = exc
            output.unlink(missing_ok=True)
            if attempt == retries:
                break
            print(f"warning: download failed ({attempt}/{retries}) for {url}: {exc}", file=sys.stderr)
            time.sleep(min(5 * attempt, 30))
    raise RuntimeError(f"download failed for {url}: {last_error}")


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


def append_provenance_record(path: Path | None, record: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"schema_version": 1, "records": []}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.setdefault("records", [])
    if not isinstance(records, list):
        raise ValueError(f"invalid provenance records in {path}")
    records.append(record)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_package_index(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": 1, "packages": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_package_index(path: Path, records: list[dict[str, object]]) -> None:
    payload = load_package_index(path)
    packages = payload.setdefault("packages", [])
    if not isinstance(packages, list):
        raise ValueError(f"invalid package index: {path}")
    packages.extend(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect_apk_package_index(
    apk_bin: Path,
    repositories: Path,
    keys_dir: Path,
    architecture: str,
    *,
    package_index: Path,
    provenance: Path,
) -> None:
    if (
        not apk_bin.is_file()
        or not repositories.is_file()
        or not keys_dir.is_dir()
        or not architecture.strip()
    ):
        raise ValueError("APK metadata inputs are incomplete")
    with tempfile.TemporaryDirectory(prefix="apk-query-") as root_dir:
        common_command = [
            str(apk_bin.resolve()),
            "--root",
            root_dir,
            "--keys-dir",
            str(keys_dir.resolve()),
            "--repositories-file",
            str(repositories.resolve()),
            "--no-cache",
        ]
        environment = os.environ.copy()
        environment["PATH"] = f"{apk_bin.resolve().parent}:{environment.get('PATH', '')}"
        init_result = subprocess.run(
            [*common_command, "add", "--arch", architecture, "--initdb", "--usermode"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if init_result.returncode:
            raise RuntimeError(
                f"APK database initialization failed ({init_result.returncode}): "
                f"{init_result.stderr.strip()}"
            )
        command = [
            *common_command,
            "query",
            "--from",
            "repositories",
            "--format",
            "json",
            "--fields",
            "name,version,license,download-url,file-size,origin",
            "*",
        ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError(
                f"APK repository query failed ({result.returncode}): {result.stderr.strip()}"
            )
    payload = json.loads(result.stdout)
    packages = payload.get("packages", []) if isinstance(payload, dict) else payload
    if not isinstance(packages, list) or not packages:
        raise ValueError("APK repository query returned no packages")
    records: list[dict[str, object]] = []
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("APK repository query returned an invalid package record")
        name = str(package.get("name", ""))
        version = str(package.get("version", ""))
        license_id = str(package.get("license", ""))
        download_url_value = package.get("download-url", "")
        if isinstance(download_url_value, list):
            download_url_value = download_url_value[0] if len(download_url_value) == 1 else ""
        download_url_value = str(download_url_value)
        if not all((name, version, download_url_value)):
            raise ValueError(f"APK package metadata is incomplete: {name or '<unknown>'}")
        if not download_url_value.startswith("https://"):
            raise ValueError(f"APK package metadata uses a non-HTTPS URL: {name}")
        records.append(
            {
                "package": name,
                "version": version,
                "license": license_id,
                "source_path": str(package.get("origin", name)),
                "download_url": download_url_value,
                "size": int(package.get("file-size", 0)),
            }
        )
    write_package_index(package_index, records)
    append_provenance_record(
        provenance,
        {
            "kind": "imagebuilder-apk-package-index",
            "repositories_sha256": sha256_file(repositories),
            "package_count": len(records),
            "verification_status": "verified-by-imagebuilder-apk-signing-keys",
        },
    )


def parse_feeds_buildinfo(payload: str) -> dict[str, dict[str, str]]:
    feeds: dict[str, dict[str, str]] = {}
    for line in payload.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0] != "src-git" or "^" not in parts[2]:
            continue
        url, commit = parts[2].rsplit("^", 1)
        if not fnmatch.fnmatch(commit, "[0-9a-f]" * 40):
            raise ValueError(f"invalid feed commit for {parts[1]}: {commit}")
        feeds[parts[1]] = {
            "repository": url.removesuffix(".git"),
            "commit": commit,
            "source": f"{url.removesuffix('.git')}/tree/{commit}",
            "archive_url": f"{url.removesuffix('.git')}/archive/{commit}.tar.gz",
        }
    if not feeds:
        raise ValueError("feeds.buildinfo contains no pinned feeds")
    return feeds


def github_repo_from_source(source: str) -> str:
    prefix = "https://github.com/"
    if not source.startswith(prefix):
        raise ValueError(f"component source is not a GitHub repository: {source}")
    repo = source[len(prefix) :].strip("/").removesuffix(".git")
    if repo.count("/") != 1:
        raise ValueError(f"invalid GitHub component source: {source}")
    return repo


def resolve_source_refs(
    *,
    components_path: Path,
    feeds_buildinfo: str,
    immortalwrt_commit: str,
    timeout: int,
    retries: int,
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{7,40}", immortalwrt_commit):
        raise ValueError(f"invalid ImmortalWrt commit: {immortalwrt_commit}")
    immortalwrt_payload = github_api_json(
        f"https://api.github.com/repos/immortalwrt/immortalwrt/commits/{immortalwrt_commit}",
        timeout=timeout,
        retries=retries,
    )
    resolved_immortalwrt_commit = str(immortalwrt_payload.get("sha", ""))
    if not fnmatch.fnmatch(resolved_immortalwrt_commit, "[0-9a-f]" * 40):
        raise ValueError(f"failed to resolve ImmortalWrt commit: {immortalwrt_commit}")
    registry = json.loads(components_path.read_text(encoding="utf-8"))
    feed_refs = parse_feeds_buildinfo(feeds_buildinfo)
    feed_refs_by_repository = {
        str(record["repository"]): record for record in feed_refs.values()
    }
    component_refs: dict[str, dict[str, str]] = {}
    for component in registry.get("components", []):
        source = str(component.get("source", ""))
        github_repo_from_source(source)
        if source == "https://github.com/immortalwrt/immortalwrt":
            component_refs[source] = {
                "repository": source,
                "ref_type": "commit",
                "ref": resolved_immortalwrt_commit,
                "source": f"{source}/tree/{resolved_immortalwrt_commit}",
                "archive_url": f"{source}/archive/{resolved_immortalwrt_commit}.tar.gz",
            }
            continue
        feed_ref = feed_refs_by_repository.get(source)
        if feed_ref:
            component_refs[source] = {
                **feed_ref,
                "ref_type": "commit",
                "ref": feed_ref["commit"],
            }
            continue
        raise ValueError(f"component source is not pinned by official build metadata: {source}")
    return {
        "schema_version": 1,
        "immortalwrt": {
            "repository": "https://github.com/immortalwrt/immortalwrt",
            "commit": resolved_immortalwrt_commit,
            "source": (
                "https://github.com/immortalwrt/immortalwrt/tree/"
                f"{resolved_immortalwrt_commit}"
            ),
            "archive_url": (
                "https://github.com/immortalwrt/immortalwrt/archive/"
                f"{resolved_immortalwrt_commit}.tar.gz"
            ),
        },
        "feeds": feed_refs,
        "components": component_refs,
    }


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


def cmd_collect_apk_package_index(args: argparse.Namespace) -> int:
    collect_apk_package_index(
        args.apk_bin,
        args.repositories,
        args.keys_dir,
        args.architecture,
        package_index=args.package_index,
        provenance=args.provenance,
    )
    print(f"collected signed APK metadata: {args.package_index}")
    return 0


def cmd_resolve_source_refs(args: argparse.Namespace) -> int:
    feeds_buildinfo = fetch_bytes(
        args.feeds_buildinfo_url,
        timeout=args.timeout,
        retries=args.retries,
    ).decode("utf-8")
    payload = resolve_source_refs(
        components_path=args.components,
        feeds_buildinfo=feeds_buildinfo,
        immortalwrt_commit=args.immortalwrt_commit,
        timeout=args.timeout,
        retries=args.retries,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"resolved exact source refs: {args.out}")
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
        checksum = target.with_name(f"{target.name}.sha256")
        checksum.write_text(f"{sha256_file(target)}  {target.name}\n", encoding="utf-8")
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
    imagebuilder_info.add_argument("--target", default="x86/generic")
    imagebuilder_info.add_argument("--github-env", type=Path)
    add_common_network_args(imagebuilder_info)
    imagebuilder_info.set_defaults(func=cmd_imagebuilder_info)

    download = subparsers.add_parser("download-url", help="download a URL with retries and optional sha256 check")
    download.add_argument("--url", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--sha256")
    add_common_network_args(download)
    download.set_defaults(func=cmd_download_url)

    collect_apk = subparsers.add_parser(
        "collect-apk-package-index",
        help="collect signed package metadata from ImageBuilder APK repositories",
    )
    collect_apk.add_argument("--apk-bin", type=Path, required=True)
    collect_apk.add_argument("--repositories", type=Path, required=True)
    collect_apk.add_argument("--keys-dir", type=Path, required=True)
    collect_apk.add_argument("--architecture", required=True)
    collect_apk.add_argument("--package-index", type=Path, required=True)
    collect_apk.add_argument("--provenance", type=Path, required=True)
    collect_apk.set_defaults(func=cmd_collect_apk_package_index)

    source_refs = subparsers.add_parser(
        "resolve-source-refs",
        help="resolve exact source commits from official build metadata",
    )
    source_refs.add_argument("--components", type=Path, required=True)
    source_refs.add_argument("--feeds-buildinfo-url", required=True)
    source_refs.add_argument("--immortalwrt-commit", required=True)
    source_refs.add_argument("--out", type=Path, required=True)
    add_common_network_args(source_refs)
    source_refs.set_defaults(func=cmd_resolve_source_refs)

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
