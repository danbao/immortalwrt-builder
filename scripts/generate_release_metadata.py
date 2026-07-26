#!/usr/bin/env python3
"""Generate release provenance, source inventory, and an SPDX package SBOM."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import sys
from pathlib import Path


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_package_manifest(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        clean = line.strip()
        if not clean:
            continue
        name, separator, version = clean.partition(" - ")
        if separator != " - " or not name or not version:
            raise ValueError(f"invalid package manifest entry in {path}:{lineno}: {line}")
        if name in packages and packages[name] != version:
            raise ValueError(f"conflicting package versions in {path}:{lineno}: {name}")
        packages[name] = version
    if not packages:
        raise ValueError(f"package manifest is empty: {path}")
    return dict(sorted(packages.items()))


def validate_component(component: dict[str, object], label: str) -> None:
    for field in ("name", "license", "source"):
        if not str(component.get(field, "")).strip():
            raise ValueError(f"{label} is missing required {field}")
    source = str(component["source"])
    if not source.startswith("https://"):
        raise ValueError(f"{label} source must use https: {source}")
    raw_source_path = str(component.get("source_path", ""))
    source_path = raw_source_path.strip("/")
    if source_path and (".." in Path(source_path).parts or raw_source_path.startswith("/")):
        raise ValueError(f"{label} has invalid source_path: {raw_source_path}")
    upstream_source = str(component.get("upstream_source", ""))
    if upstream_source and not upstream_source.startswith("https://"):
        raise ValueError(f"{label} upstream_source must use https: {upstream_source}")


def load_components(path: Path) -> dict[str, object]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    components = registry.get("components")
    if not isinstance(components, list):
        raise ValueError("component registry components must be a list")
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise ValueError(f"component {index} must be an object")
        validate_component(component, f"component {index}")
        patterns = component.get("packages")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"component {index} requires package patterns")
    return registry


def component_for_package(package: str, registry: dict[str, object]) -> dict[str, object] | None:
    for component in registry["components"]:
        if any(fnmatch.fnmatch(package, str(pattern)) for pattern in component["packages"]):
            return component
    return None


def exact_component_source(component: dict[str, object], source_refs: dict[str, object]) -> str:
    source = str(component["source"])
    record = source_refs.get("components", {}).get(source)
    exact_source = str(record.get("source", "")) if isinstance(record, dict) else ""
    if not exact_source.startswith(f"{source}/tree/"):
        raise ValueError(f"missing exact source ref for component: {component['name']}")
    source_path = str(component.get("source_path", "")).strip("/")
    return f"{exact_source.rstrip('/')}/{source_path}" if source_path else exact_source


def exact_index_source(record: dict[str, object], source_refs: dict[str, object]) -> str:
    source_path = str(record.get("source_path", "")).strip("/")
    if not source_path:
        raise ValueError(f"package index record has no source path: {record}")
    parts = source_path.split("/")
    if len(parts) >= 3 and parts[0] == "feeds":
        feed = source_refs.get("feeds", {}).get(parts[1])
        base = str(feed.get("source", "")) if isinstance(feed, dict) else ""
        relative = "/".join(parts[2:])
    else:
        core = source_refs.get("immortalwrt")
        base = str(core.get("source", "")) if isinstance(core, dict) else ""
        relative = source_path
    if "/tree/" not in base:
        raise ValueError(f"missing exact source ref for source path: {source_path}")
    return f"{base.rstrip('/')}/{relative}"


def resolved_component_registry(
    registry: dict[str, object],
    source_refs: dict[str, object],
) -> dict[str, object]:
    components = []
    for component in registry["components"]:
        components.append({**component, "exact_source": exact_component_source(component, source_refs)})
    return {"schema_version": registry.get("schema_version", 1), "components": components}


def load_package_index(path: Path) -> dict[tuple[str, str], list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[tuple[str, str], list[dict[str, object]]] = {}
    for record in payload.get("packages", []):
        key = (str(record.get("package", "")), str(record.get("version", "")))
        if not all(key) or not record.get("download_url"):
            raise ValueError(f"incomplete package index record: {record}")
        candidates = result.setdefault(key, [])
        if record not in candidates:
            candidates.append(record)
    return result


def generate_spdx(
    packages: dict[str, str],
    registry: dict[str, object],
    package_index: dict[tuple[str, str], list[dict[str, object]]],
    source_refs: dict[str, object],
    *,
    namespace: str,
) -> dict[str, object]:
    spdx_packages = []
    extracted_licenses: dict[str, dict[str, object]] = {}
    for index, (name, version) in enumerate(sorted(packages.items()), start=1):
        component = component_for_package(name, registry)
        if component is not None:
            license_id = component["license"]
            download_location = exact_component_source(component, source_refs)
            source_comment = f"source family: {component['name']}"
            version_pattern = str(component.get("version_pattern", ""))
            if version_pattern and not fnmatch.fnmatch(version, version_pattern):
                raise ValueError(
                    f"component version is not covered by reviewed metadata: "
                    f"{name} {version} (expected {version_pattern})"
                )
            upstream_source = str(component.get("upstream_source", ""))
            if upstream_source:
                source_comment += f"; upstream source: {upstream_source}"
            if str(license_id).startswith("LicenseRef-"):
                license_text = str(component.get("license_text", ""))
                if not license_text:
                    raise ValueError(f"missing extracted license text for component: {component['name']}")
                extracted_licenses[str(license_id)] = {
                    "licenseId": license_id,
                    "extractedText": license_text,
                    "name": component["name"],
                    "seeAlsos": [component["license_url"]] if component.get("license_url") else [],
                }
        else:
            candidates = package_index.get((name, version), [])
            if len(candidates) != 1:
                raise ValueError(
                    f"missing unique exact package metadata for package: "
                    f"{name} {version} ({len(candidates)} candidates)"
                )
            record = candidates[0]
            if not record.get("license") or not record.get("source_path"):
                raise ValueError(f"missing exact license/source metadata for package: {name} {version}")
            license_id = record["license"]
            download_location = exact_index_source(record, source_refs)
            source_comment = (
                f"source path: {record['source_path']}; "
                f"binary package: {record['download_url']}"
            )
        spdx_packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{index}",
                "name": name,
                "versionInfo": version,
                "downloadLocation": download_location,
                "filesAnalyzed": False,
                "licenseConcluded": license_id,
                "licenseDeclared": license_id,
                "copyrightText": "NOASSERTION",
                "comment": source_comment,
            }
        )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "ImmortalWrt builder package inventory",
        "documentNamespace": namespace,
        "creationInfo": {
            "created": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: immortalwrt-builder/generate_release_metadata.py"],
        },
        "packages": spdx_packages,
    }
    if extracted_licenses:
        document["hasExtractedLicensingInfos"] = [
            extracted_licenses[key] for key in sorted(extracted_licenses)
        ]
    return document


def release_item_for_flavor(results: dict[str, object], flavor: str) -> dict[str, object]:
    matches = []
    for item in results.get("built", []):
        if not isinstance(item, dict):
            continue
        tag = str(item.get("release_tag", ""))
        item_flavor = "daed" if "-x86-64-daed-" in tag else "standard"
        if item_flavor == flavor:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError(f"expected one build result for {flavor}, found {len(matches)}")
    item = matches[0]
    image_sha256 = str(item.get("image_sha256", ""))
    release_tag = str(item.get("release_tag", ""))
    if len(image_sha256) != 64 or not release_tag:
        raise ValueError(f"incomplete build identity for {flavor}")
    return item


def spdx_namespace(repository: str, item: dict[str, object]) -> str:
    return (
        f"https://github.com/{repository}/releases/tag/{item['release_tag']}"
        f"#spdx-{item['image_sha256']}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard-manifest", type=Path, required=True)
    parser.add_argument("--daed-manifest", type=Path, required=True)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--package-index", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--source-refs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--imagebuilder-version", required=True)
    parser.add_argument("--imagebuilder-sha256", required=True)
    parser.add_argument("--immortalwrt-version-code", required=True)
    parser.add_argument("--immortalwrt-commit", required=True)
    parser.add_argument("--build-date", required=True)
    parser.add_argument("--repository", required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        standard = parse_package_manifest(args.standard_manifest)
        daed = parse_package_manifest(args.daed_manifest)
        registry = load_components(args.components)
        package_index = load_package_index(args.package_index)
        provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
        source_refs = json.loads(args.source_refs.read_text(encoding="utf-8"))
        results = json.loads(args.results.read_text(encoding="utf-8"))
        common_metadata = {
                "schema_version": 1,
                "repository": args.repository,
                "build_date": args.build_date,
                "imagebuilder": {
                    "version": args.imagebuilder_version,
                    "sha256": args.imagebuilder_sha256,
                },
                "immortalwrt": {
                    "version_code": args.immortalwrt_version_code,
                    "commit": args.immortalwrt_commit,
                },
                "results": results,
        }
        for flavor, packages in (("standard", standard), ("daed", daed)):
            release_item = release_item_for_flavor(results, flavor)
            flavor_dir = args.out_dir / flavor
            write_json(
                flavor_dir / "build-metadata.json",
                {**common_metadata, "flavor": flavor, "release": release_item},
            )
            write_json(flavor_dir / "upstream-provenance.json", provenance)
            write_json(
                flavor_dir / "third-party-sources.json",
                {
                    **resolved_component_registry(registry, source_refs),
                    "upstream_source_refs": source_refs,
                },
            )
            write_json(
                flavor_dir / "packages.spdx.json",
                generate_spdx(
                    packages,
                    registry,
                    package_index,
                    source_refs,
                    namespace=spdx_namespace(args.repository, release_item),
                ),
            )
        print(f"generated release metadata: {args.out_dir}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
