#!/usr/bin/env python3
"""Preflight helpers for the OpenWrt ImageBuilder workflow."""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


DEFAULT_USER_AGENT = "danbao-openwrt-builder"
MAX_MIRRORED_FEED_BYTES = 2 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_TAR_MEMBERS = 10_000
MAX_PACKAGE_INDEX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    required: bool
    verification: str = "require-signature"
    source: str = ""


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
        if len(parts) not in {3, 4, 5}:
            raise ValueError(f"expected 3 to 5 tab-separated fields in {path}:{lineno}")
        name, url, required = (part.strip() for part in parts[:3])
        verification = parts[3].strip() if len(parts) == 4 else "require-signature"
        if len(parts) == 5:
            verification = parts[3].strip()
        source = parts[4].strip() if len(parts) == 5 else ""
        if verification not in {"require-signature", "allow-untrusted"}:
            raise ValueError(f"invalid feed verification policy in {path}:{lineno}: {verification}")
        if not name or not url:
            raise ValueError(f"feed name/url cannot be empty in {path}:{lineno}")
        if name in seen:
            raise ValueError(f"duplicate feed in {path}:{lineno}: {name}")
        seen.add(name)
        feeds.append(
            Feed(
                name=name,
                url=url.rstrip("/"),
                required=parse_bool(required),
                verification=verification,
                source=source,
            )
        )
    if not feeds:
        raise ValueError(f"feed list is empty: {path}")
    return feeds


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


def safe_extract_tar(archive_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_TAR_MEMBERS:
            raise ValueError(f"tar archive has too many members: {len(members)}")
        total_size = 0
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"tar archive contains unsupported member: {member.name}")
            total_size += member.size
            if total_size > MAX_EXTRACTED_BYTES:
                raise ValueError(f"tar archive exceeds extracted size limit: {total_size} bytes")
        archive.extractall(target, filter="data")


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


def wait_for_release_asset(
    api_url: str,
    pattern: str,
    *,
    timeout: int,
    retries: int,
    attempts: int,
    delay: float,
) -> tuple[dict, dict]:
    if attempts < 1:
        raise ValueError("release asset wait attempts must be at least 1")
    if delay < 0:
        raise ValueError("release asset wait delay must not be negative")
    for attempt in range(1, attempts + 1):
        release = github_api_json(api_url, timeout=timeout, retries=retries)
        assets = release.get("assets", [])
        matches = [
            asset
            for asset in assets
            if fnmatch.fnmatch(str(asset.get("name", "")), pattern)
        ]
        if len(matches) == 1:
            return release, matches[0]
        if matches or attempt == attempts:
            select_release_asset(assets, pattern)
        print(
            f"::notice::release asset matching {pattern} is not published yet; "
            f"waiting {delay:g}s ({attempt}/{attempts})",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise AssertionError("release asset wait loop ended unexpectedly")


def select_release_asset_by_id(assets: list[dict], asset_id: object) -> dict:
    matches = [asset for asset in assets if asset.get("id") == asset_id]
    if len(matches) != 1:
        raise RuntimeError(f"release asset id {asset_id} disappeared during download")
    return matches[0]


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


def verify_release_asset(
    asset: dict[str, object],
    path: Path,
    *,
    allow_missing_digest: bool,
) -> dict[str, object]:
    expected_size = int(asset.get("size") or 0)
    if path.stat().st_size != expected_size:
        raise ValueError(
            f"size mismatch for {path}: expected {expected_size}, got {path.stat().st_size}"
        )
    actual = sha256_file(path)
    api_digest = str(asset.get("digest") or "")
    if not api_digest:
        if not allow_missing_digest:
            raise ValueError(f"release asset does not provide a digest: {asset.get('name')}")
        status = "unverified-upstream"
    else:
        algorithm, separator, expected = api_digest.partition(":")
        if separator != ":" or algorithm != "sha256" or len(expected) != 64:
            raise ValueError(f"unsupported release asset digest: {api_digest}")
        if actual != expected:
            raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")
        status = "verified-api-digest"
    return {
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name"),
        "api_digest": asset.get("digest"),
        "sha256": actual,
        "size": path.stat().st_size,
        "updated_at": asset.get("updated_at"),
        "verification_status": status,
    }


def ensure_release_asset_unchanged(
    release_before: dict[str, object],
    asset_before: dict[str, object],
    release_after: dict[str, object],
    asset_after: dict[str, object],
) -> None:
    release_fields = ("id", "tag_name")
    asset_fields = ("id", "name", "size", "updated_at", "digest")
    before = (
        tuple(release_before.get(field) for field in release_fields),
        tuple(asset_before.get(field) for field in asset_fields),
    )
    after = (
        tuple(release_after.get(field) for field in release_fields),
        tuple(asset_after.get(field) for field in asset_fields),
    )
    if before != after:
        raise RuntimeError(f"release asset changed during download: {asset_before.get('name')}")


def parse_stanzas(payload: str) -> list[dict[str, str]]:
    stanzas: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in [*payload.splitlines(), ""]:
        if not line.strip():
            if current:
                stanzas.append(current)
                current = {}
            continue
        if line[:1].isspace():
            continue
        key, separator, value = line.partition(":")
        if separator:
            current[key.strip()] = value.strip()
    return stanzas


def parse_package_index(
    payload: str,
    base_url: str,
    *,
    require_compliance: bool = True,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for stanza in parse_stanzas(payload):
        package = stanza.get("Package", "")
        if not package:
            continue
        required_fields = ["Version", "Filename", "SHA256sum", "Size"]
        if require_compliance:
            required_fields.extend(["License", "Source"])
        for field in required_fields:
            if not stanza.get(field):
                raise ValueError(f"package {package} is missing {field}")
        digest = stanza["SHA256sum"]
        if not fnmatch.fnmatch(digest, "[0-9a-f]" * 64):
            raise ValueError(f"package {package} has invalid SHA256sum")
        filename = stanza["Filename"]
        filename_path = PurePosixPath(filename)
        if filename_path.is_absolute() or ".." in filename_path.parts:
            raise ValueError(f"package {package} has unsafe Filename: {filename}")
        records.append(
            {
                "package": package,
                "version": stanza["Version"],
                "license": stanza.get("License", ""),
                "source_path": stanza.get("Source", ""),
                "filename": filename,
                "sha256": digest,
                "size": int(stanza["Size"]),
                "download_url": f"{base_url.rstrip('/')}/{filename}",
            }
        )
    if not records:
        raise ValueError(f"package index is empty: {base_url}")
    return records


def bounded_gzip_decompress(payload: bytes, *, max_bytes: int = MAX_PACKAGE_INDEX_BYTES) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
        decompressed = archive.read(max_bytes + 1)
    if len(decompressed) > max_bytes:
        raise ValueError(f"gzip payload exceeds decompressed size limit: {max_bytes} bytes")
    return decompressed


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


def mirror_feed(
    feed: Feed,
    *,
    output_dir: Path,
    package_index: Path,
    provenance: Path,
    timeout: int,
    retries: int,
) -> None:
    packages_url = f"{feed.url}/Packages.gz"
    manifest_url = f"{feed.url}/Packages.manifest"
    signature_url = f"{feed.url}/Packages.sig"
    packages_before = fetch_bytes(packages_url, timeout=timeout, retries=retries)
    manifest_before = fetch_bytes(manifest_url, timeout=timeout, retries=retries)
    signature_before = fetch_bytes(signature_url, timeout=timeout, retries=retries)
    package_records = parse_package_index(
        bounded_gzip_decompress(packages_before).decode("utf-8"),
        feed.url,
        require_compliance=False,
    )
    records = parse_package_index(
        manifest_before.decode("utf-8"),
        feed.url,
        require_compliance=False,
    )
    identity_fields = ("version", "filename", "sha256", "size")
    manifest_identity = {
        str(record["package"]): tuple(record[field] for field in identity_fields)
        for record in records
    }
    package_identity = {
        str(record["package"]): tuple(record[field] for field in identity_fields)
        for record in package_records
    }
    if manifest_identity != package_identity:
        raise RuntimeError(f"Packages.manifest does not match Packages.gz: {feed.name}")
    total_size = sum(int(record["size"]) for record in records)
    if total_size > MAX_MIRRORED_FEED_BYTES:
        raise ValueError(f"feed mirror exceeds size limit: {feed.name} ({total_size} bytes)")
    output_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        target = output_dir / str(record["filename"])
        download_url(
            str(record["download_url"]),
            target,
            timeout=timeout,
            retries=retries,
            max_bytes=int(record["size"]),
        )
        if target.stat().st_size != record["size"]:
            raise ValueError(f"package size mismatch: {target}")
        actual = sha256_file(target)
        if actual != record["sha256"]:
            raise ValueError(f"package sha256 mismatch: {target}")
    packages_after = fetch_bytes(packages_url, timeout=timeout, retries=retries)
    manifest_after = fetch_bytes(manifest_url, timeout=timeout, retries=retries)
    signature_after = fetch_bytes(signature_url, timeout=timeout, retries=retries)
    if (
        packages_before != packages_after
        or manifest_before != manifest_after
        or signature_before != signature_after
    ):
        raise RuntimeError(f"feed changed while mirroring: {feed.name}")
    write_package_index(package_index, records)
    append_provenance_record(
        provenance,
        {
            "kind": "mirrored-package-feed",
            "name": feed.name,
            "url": feed.url,
            "package_count": len(records),
            "packages_sha256": hashlib.sha256(packages_before).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_before).hexdigest(),
            "signature_sha256": hashlib.sha256(signature_before).hexdigest(),
            "verification_status": "hash-verified-packages-untrusted-signing-key",
        },
    )


def repository_urls(path: Path) -> list[tuple[str, str]]:
    repositories: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "src/gz":
            repositories.append((parts[1], parts[2].rstrip("/")))
    if not repositories:
        raise ValueError(f"no package repositories found: {path}")
    return repositories


def collect_package_indexes(
    repositories_conf: Path,
    *,
    package_index: Path,
    provenance: Path,
    timeout: int,
    retries: int,
) -> None:
    for name, url in repository_urls(repositories_conf):
        manifest_url = f"{url}/Packages.manifest"
        packages_url = f"{url}/Packages.gz"
        manifest_payload = fetch_bytes(manifest_url, timeout=timeout, retries=retries)
        packages_payload = fetch_bytes(packages_url, timeout=timeout, retries=retries)
        manifest_records = parse_package_index(
            manifest_payload.decode("utf-8"),
            url,
            require_compliance=False,
        )
        package_records = parse_package_index(
            bounded_gzip_decompress(packages_payload).decode("utf-8"),
            url,
            require_compliance=False,
        )
        identity_fields = ("version", "filename", "sha256", "size")
        manifest_identity = {
            str(record["package"]): tuple(record[field] for field in identity_fields)
            for record in manifest_records
        }
        package_identity = {
            str(record["package"]): tuple(record[field] for field in identity_fields)
            for record in package_records
        }
        if manifest_identity != package_identity:
            raise RuntimeError(f"Packages.manifest does not match Packages.gz: {name}")
        write_package_index(package_index, manifest_records)
        append_provenance_record(
            provenance,
            {
                "kind": "imagebuilder-package-index",
                "name": name,
                "url": url,
                "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
                "packages_sha256": hashlib.sha256(packages_payload).hexdigest(),
                "package_count": len(manifest_records),
                "verification_status": "metadata-cross-checked-with-package-index",
            },
        )


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
    feed_file: Path | None,
    provenance_path: Path,
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
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    release_tags = {
        str(record.get("repository")): str(record.get("release_tag"))
        for record in provenance.get("records", [])
        if record.get("kind") == "github-release-asset"
        and record.get("repository")
        and record.get("release_tag")
    }
    feed_refs = parse_feeds_buildinfo(feeds_buildinfo)
    feed_refs_by_repository = {
        str(record["repository"]): record for record in feed_refs.values()
    }
    component_refs: dict[str, dict[str, str]] = {}
    for component in registry.get("components", []):
        source = str(component.get("source", ""))
        repo = github_repo_from_source(source)
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
        tag = release_tags.get(repo)
        if tag:
            component_refs[source] = {
                "repository": source,
                "ref_type": "release-tag",
                "ref": tag,
                "source": f"{source}/tree/{tag}",
                "archive_url": f"{source}/archive/refs/tags/{tag}.tar.gz",
            }
            continue
        commit_payload = github_api_json(
            f"https://api.github.com/repos/{repo}/commits/HEAD",
            timeout=timeout,
            retries=retries,
        )
        commit = str(commit_payload.get("sha", ""))
        if not fnmatch.fnmatch(commit, "[0-9a-f]" * 40):
            raise ValueError(f"failed to resolve source commit for {repo}")
        component_refs[source] = {
            "repository": source,
            "ref_type": "commit",
            "ref": commit,
            "source": f"{source}/tree/{commit}",
            "archive_url": f"{source}/archive/{commit}.tar.gz",
            "artifact_source_relation": "unverified-upstream",
        }
    for feed in (read_feeds(feed_file) if feed_file else []):
        if not feed.source:
            raise ValueError(f"feed has no source repository: {feed.name}")
        repo = github_repo_from_source(feed.source)
        commit_payload = github_api_json(
            f"https://api.github.com/repos/{repo}/commits/HEAD",
            timeout=timeout,
            retries=retries,
        )
        commit = str(commit_payload.get("sha", ""))
        if not fnmatch.fnmatch(commit, "[0-9a-f]" * 40):
            raise ValueError(f"failed to resolve feed source commit for {feed.name}")
        feed_refs[feed.name] = {
            "repository": feed.source,
            "commit": commit,
            "source": f"{feed.source}/tree/{commit}",
            "archive_url": f"{feed.source}/archive/{commit}.tar.gz",
            "artifact_source_relation": "unverified-upstream",
        }
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


def check_feed(
    feed: Feed,
    *,
    timeout: int,
    retries: int,
    provenance: Path | None = None,
) -> bool:
    url = f"{feed.url}/Packages.gz"
    print(f"Probing feed {feed.name}: {url}")
    try:
        packages = fetch_bytes(url, timeout=timeout, retries=retries)
        signature = fetch_bytes(f"{feed.url}/Packages.sig", timeout=timeout, retries=retries)
    except Exception as exc:
        message = f"feed unreachable: {feed.name} ({url}): {exc}"
        if feed.required:
            print(f"::error::{message}", file=sys.stderr)
            return False
        print(f"warning: optional {message}", file=sys.stderr)
        return True
    verification_status = (
        "signature-present-untrusted-key"
        if feed.verification == "allow-untrusted"
        else "signature-required"
    )
    append_provenance_record(
        provenance,
        {
            "kind": "package-feed",
            "name": feed.name,
            "url": feed.url,
            "packages_sha256": hashlib.sha256(packages).hexdigest(),
            "signature_sha256": hashlib.sha256(signature).hexdigest(),
            "verification_status": verification_status,
        },
    )
    print(f"  ok {feed.name} reachable ({verification_status})")
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


def cmd_mirror_feeds(args: argparse.Namespace) -> int:
    for feed in read_feeds(args.feed_file):
        mirror_feed(
            feed,
            output_dir=args.dir,
            package_index=args.package_index,
            provenance=args.provenance,
            timeout=args.timeout,
            retries=args.retries,
        )
    return 0


def cmd_collect_package_indexes(args: argparse.Namespace) -> int:
    collect_package_indexes(
        args.repositories_conf,
        package_index=args.package_index,
        provenance=args.provenance,
        timeout=args.timeout,
        retries=args.retries,
    )
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
        feed_file=args.feed_file,
        provenance_path=args.provenance,
        feeds_buildinfo=feeds_buildinfo,
        immortalwrt_commit=args.immortalwrt_commit,
        timeout=args.timeout,
        retries=args.retries,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"resolved exact source refs: {args.out}")
    return 0


def cmd_check_feeds(args: argparse.Namespace) -> int:
    feeds = read_feeds(args.feed_file)
    results = [
        check_feed(
            feed,
            timeout=args.timeout,
            retries=args.retries,
            provenance=args.provenance,
        )
        for feed in feeds
    ]
    if not all(results):
        raise RuntimeError("one or more required feeds are unreachable")
    return 0


def cmd_download_release_asset(args: argparse.Namespace) -> int:
    api_url = release_api_url(args.repo, args.tag)
    release, asset = wait_for_release_asset(
        api_url,
        args.pattern,
        timeout=args.timeout,
        retries=args.retries,
        attempts=args.asset_wait_attempts,
        delay=args.asset_wait_delay,
    )
    url = str(asset.get("url") or "")
    if not url:
        raise ValueError(f"release asset has no API download URL: {asset.get('name')}")
    output = args.dir / str(asset["name"])
    output.parent.mkdir(parents=True, exist_ok=True)
    expected_size = int(asset.get("size") or 0)
    if expected_size <= 0:
        raise ValueError(f"release asset has invalid size: {asset.get('name')}")
    download_url(
        url,
        output,
        timeout=args.timeout,
        retries=args.retries,
        accept="application/octet-stream",
        max_bytes=expected_size,
    )
    record = verify_release_asset(
        asset,
        output,
        allow_missing_digest=args.allow_missing_digest,
    )
    release_after = github_api_json(api_url, timeout=args.timeout, retries=args.retries)
    asset_after = select_release_asset_by_id(release_after.get("assets", []), asset.get("id"))
    ensure_release_asset_unchanged(release, asset, release_after, asset_after)
    record.update(
        {
            "kind": "github-release-asset",
            "repository": args.repo,
            "release_id": release.get("id"),
            "release_tag": release.get("tag_name"),
        }
    )
    append_provenance_record(args.provenance, record)
    if record["verification_status"] == "unverified-upstream":
        print(
            f"::warning::upstream asset has no API digest: {args.repo}@"
            f"{release.get('tag_name')} {asset.get('name')}",
            file=sys.stderr,
        )
    print(
        f"downloaded release asset: {args.repo}@{release.get('tag_name')} "
        f"{output} ({record['verification_status']})"
    )
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


def cmd_safe_extract_tar(args: argparse.Namespace) -> int:
    safe_extract_tar(args.archive, args.target)
    print(f"safely extracted: {args.archive} -> {args.target}")
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

    mirror = subparsers.add_parser("mirror-feeds", help="mirror untrusted feeds into verified local packages")
    mirror.add_argument("--feed-file", type=Path, required=True)
    mirror.add_argument("--dir", type=Path, required=True)
    mirror.add_argument("--package-index", type=Path, required=True)
    mirror.add_argument("--provenance", type=Path, required=True)
    add_common_network_args(mirror)
    mirror.set_defaults(func=cmd_mirror_feeds)

    collect_indexes = subparsers.add_parser(
        "collect-package-indexes",
        help="collect exact license/source metadata from ImageBuilder repositories",
    )
    collect_indexes.add_argument("--repositories-conf", type=Path, required=True)
    collect_indexes.add_argument("--package-index", type=Path, required=True)
    collect_indexes.add_argument("--provenance", type=Path, required=True)
    add_common_network_args(collect_indexes)
    collect_indexes.set_defaults(func=cmd_collect_package_indexes)

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
        help="resolve exact upstream source commits and release tags",
    )
    source_refs.add_argument("--components", type=Path, required=True)
    source_refs.add_argument("--feed-file", type=Path)
    source_refs.add_argument("--provenance", type=Path, required=True)
    source_refs.add_argument("--feeds-buildinfo-url", required=True)
    source_refs.add_argument("--immortalwrt-commit", required=True)
    source_refs.add_argument("--out", type=Path, required=True)
    add_common_network_args(source_refs)
    source_refs.set_defaults(func=cmd_resolve_source_refs)

    check_feeds = subparsers.add_parser("check-feeds", help="probe configured feed Packages.gz files")
    check_feeds.add_argument("--feed-file", type=Path, required=True)
    check_feeds.add_argument("--provenance", type=Path)
    add_common_network_args(check_feeds)
    check_feeds.set_defaults(func=cmd_check_feeds)

    download_asset = subparsers.add_parser("download-release-asset", help="download exactly one GitHub Release asset")
    download_asset.add_argument("--repo", required=True)
    download_asset.add_argument("--tag", default="latest")
    download_asset.add_argument("--pattern", required=True)
    download_asset.add_argument("--dir", type=Path, required=True)
    download_asset.add_argument("--provenance", type=Path)
    download_asset.add_argument("--allow-missing-digest", action="store_true")
    download_asset.add_argument("--asset-wait-attempts", type=int, default=1)
    download_asset.add_argument("--asset-wait-delay", type=float, default=10)
    add_common_network_args(download_asset)
    download_asset.set_defaults(func=cmd_download_release_asset)

    copy_raw_images = subparsers.add_parser("copy-raw-images", help="copy built raw images to their release asset names")
    copy_raw_images.add_argument("--results", type=Path, required=True)
    copy_raw_images.add_argument("--source-dir", type=Path, required=True)
    copy_raw_images.add_argument("--out-dir", type=Path, required=True)
    copy_raw_images.set_defaults(func=cmd_copy_raw_images)

    extract_tar = subparsers.add_parser("safe-extract-tar", help="extract an untrusted tar archive safely")
    extract_tar.add_argument("--archive", type=Path, required=True)
    extract_tar.add_argument("--target", type=Path, required=True)
    extract_tar.set_defaults(func=cmd_safe_extract_tar)

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
