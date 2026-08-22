# Official daed Migration and CI Hardening Plan

## Status

- Status: approved for implementation
- Scope: ImmortalWrt ImageBuilder validation, image conversion, release publishing, and supply-chain metadata
- Build topology: one build profile, no matrix, no reusable workflow
- Priority: security first; migration to the official ImmortalWrt daed packages is mandatory

## Goals

This plan hardens the firmware build and release pipeline while preserving the
project's existing single-profile workflow and runtime network behavior. The
implementation must:

1. Replace the unsigned third-party daede package path with official
   ImmortalWrt packages, with no fallback.
2. Separate untrusted or self-hosted build execution from GitHub write
   permissions.
3. Publish enough checksums, manifests, SBOM data, and provenance metadata to
   audit every released image.
4. Make validation, dry-run builds, publishing, and post-release record repair
   deterministic and testable.
5. Keep the current release family, first-boot networking behavior, and one
   x86_64 build profile.

## Locked Decisions

- Use only official `daed`, `daed-geoip`, `daed-geosite`, `luci-app-daed`, and
  `luci-i18n-daed-zh-cn` packages from the verified ImmortalWrt ImageBuilder.
- Delete the old third-party feed configuration, downloader, parser, tests, and
  documentation. Do not retain a compatibility or emergency fallback.
- Keep a single `generic` profile with a 2048 MiB root filesystem and one NIC.
- Replace the old `publish_release` input with a single `build_mode` choice:
  `validate`, `dry-run`, or `publish`.
- Run publishing only on GitHub-hosted `ubuntu-24.04`, even when the build job
  runs on a self-hosted runner.
- Keep existing release tags, naming, daed release family, and the limit of 30
  managed releases.
- Keep the existing first-boot network policy. LAN DHCP and RA remain disabled,
  and the user continues to configure the static address.
- Treat the absence of subscription UI parity as acceptable. Official daed
  service operation, supported configuration, eBPF transparent proxying, and
  bypass forwarding are the functional acceptance criteria.

## 1. Central Build Profile

Add `config/build-profile.json` as the single source of truth for the build.
Use a versioned schema and include at least:

- profile name and schema version;
- ImageBuilder profile: `generic`;
- root filesystem size: 2048 MiB;
- NIC count: 1;
- expected output pattern: `*squashfs-combined-efi.img.gz`;
- requested package list;
- required package list;
- forbidden package list.

The requested package set must contain the current core packages plus:

- `luci-i18n-firewall-zh-cn`;
- `luci-i18n-package-manager-zh-cn`;
- `daed`;
- `daed-geoip`;
- `daed-geosite`;
- `luci-app-daed`;
- `luci-i18n-daed-zh-cn`.

The forbidden list must include `luci-app-daede` and any proxy stack known to
conflict with the selected daed configuration.

Extend `scripts/openwrt_build_preflight.py` so it:

1. Loads and validates the profile before invoking ImageBuilder.
2. Rejects malformed fields, duplicate packages, missing required packages,
   and forbidden packages.
3. Resolves and verifies the ImageBuilder archive and its SHA256 checksum.
4. Confirms the ImageBuilder enables package signatures, signature checking,
   TLS certificate checking, image manifests, and CycloneDX SBOM generation.
5. Runs `make manifest` and proves that the complete official daed package set
   is available.
6. Stops immediately if any requirement fails; it must never fall back to the
   old unsigned feed.

Remove `config/daed-feed.json` and all code, tests, and documentation dedicated
to downloading `daed` or `luci-app-daede` from the old third-party source.

## 2. Workflow Modes and Permission Separation

Refactor `.github/workflows/build-openwrt.yml` around these modes:

### `validate`

- Resolve and checksum-verify the ImageBuilder.
- Validate the build profile and required ImageBuilder security options.
- Run `make manifest` and validate the official package set.
- Do not build an image, convert an OVA, or create a release.

### `dry-run`

- Perform the complete IMG and OVA build.
- Generate all release metadata and verification files.
- Upload only a short-lived GitHub Actions artifact.
- Do not create or update a GitHub Release.

### `publish`

- Perform the complete build and conversion.
- Transfer the prepared release payload to the isolated publish job.
- Publish or idempotently update the GitHub Release.
- Update the converted-image manifest and rendered documentation.

Manual dispatch defaults to `publish`; scheduled builds always use `publish`.

Split the workflow into two security boundaries:

1. The build job has `contents: read`. It may use the selected self-hosted
   runner for manual builds; scheduled builds continue to use
   `ubuntu-24.04`. Checkout must use `persist-credentials: false`.
2. The publish job runs only on `ubuntu-24.04` and is the only job with
   `contents: write`. It downloads the prepared build payload and performs all
   release and repository-write operations.

If no new image is produced, publishing and record updates are skipped and the
workflow succeeds with a clear Job Summary message.

## 3. Artifact Retention

Use separate artifact policies by purpose:

- publish handoff payload: 1 day;
- dry-run output: 14 days;
- validation or failure diagnostics: 7 days.

Use the lowest useful compression level for already-compressed IMG and OVA
files to reduce runner time and CPU cost.

## 4. Release Assets and Provenance

Each release must directly expose:

- the compressed raw image (`.img.gz`);
- the ESXi appliance (`.ova`);
- the existing OVA checksum (`.ova.sha256`);
- a complete `SHA256SUMS` file;
- the final image package manifest (`*.manifest`);
- the CycloneDX SBOM (`*.bom.cdx.json`);
- `build-metadata.json`;
- `build-metadata.tar.gz`.

Package lower-level ImageBuilder evidence such as `config.buildinfo` and
`feeds.buildinfo` in `build-metadata.tar.gz`. Produce this archive
deterministically using stable path ordering, timestamps, ownership, and group
metadata.

`build-metadata.json` must use a versioned schema and record at least:

- `source=official-immortalwrt`;
- selected profile, root filesystem size, and NIC count;
- ImageBuilder version, download URL, archive SHA256, upstream version, and
  upstream commit when available;
- versions of the official daed packages resolved from the manifest;
- source repository commit, workflow run URL, and runner type;
- names and SHA256 checksums of published assets.

Extend `dist/build-results.json` with an explicit `release_assets` list and the
new provenance fields. Downstream publishing must consume this declaration
instead of guessing asset names.

Release notes should summarize the official package source, daed versions,
profile, root filesystem size, NIC count, and workflow run URL.

## 5. Idempotent Publishing and Record Repair

Refactor `scripts/publish_releases.py` so it:

- validates that `release_assets` is present and non-empty;
- uploads every declared asset when creating a new Release;
- verifies the exact set of managed asset names after publication;
- removes stale managed assets when required;
- preserves assets that are not managed by this workflow;
- treats an existing Release as immutable: every expected remote GitHub digest
  must match before an idempotent rerun succeeds, and historical assets are
  never overwritten with `--clobber`;
- keeps current tags, release naming, family grouping, and retention behavior.

If release publication succeeds but committing or pushing the generated
manifest and documentation fails:

1. Keep the successful Release; do not roll it back.
2. Fail the workflow loudly and remove any error-swallowing behavior such as
   `|| exit 0`.
3. Fetch the latest `main` branch.
4. Regenerate records deterministically from the same `build-results.json`.
5. Retry the push up to three times.
6. Allow a later rerun to repair repository records without creating a
   duplicate Release.

## 6. Actions Pinning and Dependency Updates

Pin official actions to full commit SHAs. The initially approved versions are:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1`
  (`v7.0.1`);
- `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`
  (`v7.0.1`);
- `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`
  (`v8.0.1`).

Add monthly Dependabot updates for the `github-actions` ecosystem so action
upgrades arrive as reviewable pull requests. Self-hosted runners must satisfy
the Node.js requirements of the pinned actions; no legacy compatibility branch
will be maintained.

## 7. Lightweight Repository CI

Add a separate lightweight workflow for pull requests and pushes to `main`.
Grant only `contents: read` and run:

```sh
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
bash -n files/etc/uci-defaults/*.sh
```

Also parse every tracked JSON configuration file. This workflow must not
download ImageBuilder archives, perform network-dependent firmware builds, or
publish releases.

Add `.gitattributes` rules to normalize line endings for Python, Shell, JSON,
YAML, and Markdown files.

## 8. Automated Test Coverage

Add or update tests for:

- build-profile schema validation;
- missing, duplicate, required, and forbidden package handling;
- ImageBuilder security-option assertions;
- success and failure of the official daed manifest check;
- rejection of the old third-party daede packages;
- output selection when zero, one, or multiple images are present;
- matching and required image manifest and SBOM files;
- deterministic metadata archives;
- `SHA256SUMS` contents and checksum correctness;
- required `build-metadata.json` fields;
- generalized release asset upload and verification;
- idempotent release updates;
- stale managed asset removal and unmanaged asset preservation;
- record-push retries and non-zero failure after release success.

## 9. Manual Acceptance Gate

Run these checks before considering the migration complete:

1. Run `validate` and confirm the official daed package set and every required
   security option.
2. Run `dry-run` and inspect all expected artifacts, checksums, manifests,
   SBOM data, and provenance metadata.
3. Import the OVA into ESXi and verify VmxNet3, boot, LuCI, the official daed
   page, service startup and restart, configuration application, eBPF
   transparent proxying, bypass forwarding, and reboot persistence.
4. Import the raw IMG into PVE and verify boot and basic networking. Do not add
   `qemu-ga` as part of this work.
5. Run `publish` and verify release notes, every release asset, remote asset
   validation, manifest recording, and generated documentation.
6. Rerun the same build and confirm that publication is idempotent, no duplicate
   release is created, and an interrupted record update can be repaired.

Document any official LuCI functionality that differs from the former daede UI
and provide the supported manual configuration steps.

## 10. Failure Policy

The pipeline must fail before publishing when any of the following occurs:

- the official daed package set is unavailable;
- a required ImageBuilder security option is disabled;
- the manifest, SBOM, or expected image is missing;
- more than one image matches the declared output pattern;
- a checksum does not match;
- the build-to-publish handoff is incomplete.

There is no third-party package fallback. A successful Release remains
available if only repository record publication fails; the workflow reports the
failure and a rerun repairs the records.

## Non-Goals

This implementation will not:

- introduce a build matrix or reusable workflow;
- change the selected target, profile, root filesystem size, or NIC count;
- add Docker-based ImageBuilder execution or build caches;
- add `qemu-ga`, nano, an extra curl package, or Docker tooling to the image;
- change first-boot LAN, DHCP, RA, WAN, or static-address behavior;
- keep the old unsigned package feed as a fallback;
- rename existing release tags or rewrite historical releases;
- add speculative features unrelated to build security, reproducibility, or
  release verification.

## Implementation Order

1. Add the build profile and profile-validation tests.
2. Migrate preflight checks to official daed and remove the old feed path.
3. Add metadata, SBOM, manifest, and checksum asset preparation.
4. Generalize release publishing and record-repair behavior.
5. Split the workflow into build and publish jobs and add build modes.
6. Pin actions, add Dependabot, `.gitattributes`, and lightweight CI.
7. Update README and operational documentation.
8. Run automated tests, `validate`, `dry-run`, ESXi/PVE acceptance, and final
   `publish` verification.

Implementation must stop at any failed security or artifact-integrity gate and
fix that failure before proceeding to publication.
