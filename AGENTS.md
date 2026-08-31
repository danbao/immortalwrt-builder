# Repository Guidelines

## Project Structure & Module Organization

This repository automates building ImmortalWrt 25.12.1 x86_64 images, converting them to ESXi-ready OVA files, and publishing release artifacts. Core automation lives in `scripts/`: `openwrt_build_preflight.py` resolves/downloads the ImageBuilder, validates the centralized build profile, ImageBuilder security options, and official package manifest, and lists the release tags already published; `openwrt_img_to_ova.py` handles image discovery, dedup against published tags, conversion, release assets, provenance metadata, and checksums; `publish_releases.py` validates and creates GitHub Releases from explicitly declared assets. The GitHub Actions entrypoint is `.github/workflows/build-openwrt.yml`, and the single build profile is `config/build-profile.json`. CI never commits anything back to the repository: the published Releases are the only build state. Build outputs such as `dist/`, `build-out/`, `imagebuilder/`, `*.ova`, and `*.vmdk` must stay out of git.

## Build, Test, and Development Commands

- `sudo apt-get install -y qemu-utils`: installs `qemu-img`, required for local conversion.
- `python3 scripts/openwrt_build_preflight.py validate-profile --config config/build-profile.json`: validates the centralized single build profile and prints workflow values.
- `python3 scripts/openwrt_build_preflight.py validate-imagebuilder --profile config/build-profile.json --config imagebuilder/.config`: asserts the required signature, TLS, manifest, and CycloneDX SBOM options.
- `python3 scripts/openwrt_build_preflight.py validate-manifest --profile config/build-profile.json --manifest build-out/official.packages.manifest --metadata-out build-out/official-packages.json`: validates the official package set declared in the build profile and records versions.
- `python3 scripts/openwrt_build_preflight.py release-tags --repo <owner/name> --output dist/published-tags.json`: records the release tags already published, which is the dedup input for `scan`.
- `python3 scripts/openwrt_img_to_ova.py scan --img-dir <dir-with-img-files> --out-dir dist --results dist/build-results.json --nic-count 1`: converts `.img` or `.img.gz` files into OVA artifacts. Add `--known-tags dist/published-tags.json` to skip images that already have a Release. CI also passes `--release-date`, `--immortalwrt-version-code`, `--immortalwrt-commit`, and `--repository-commit`.
- `python3 scripts/publish_releases.py dist/build-results.json --keep-releases 30 --expected-repository-commit <commit> --expected-workflow-run-url <url>`: validates the release handoff against trusted run provenance, publishes all declared assets, and prunes older managed releases; requires authenticated `gh`.
- `python3 -m py_compile scripts/*.py`: quick syntax check for script-only changes.
- `python3 -m unittest discover -s tests`: runs the unit tests.

## Coding Style & Naming Conventions

Use Python 3 standard library only unless a new dependency is justified. Follow the existing style: 4-space indentation, type hints, `Path` for filesystem paths, small pure helpers, and explicit subprocess argument lists. Keep CLI flags kebab-cased, generated filenames lowercase and descriptive, and manifest fields stable because CI and release publishing consume them.

## Testing Guidelines

Run `python3 -m unittest discover -s tests` for script changes; network-dependent commands are tested by mocking `fetch_bytes`/`download_url`. For conversion logic, release tags, or asset names — including any change to the build profile `name` — bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py`; it is a provenance marker only, since dedup now keys on the builder commit carried in the release tag. Any change to the tag format must be reflected in `published_tag_pattern` (dedup matching) and in `RELEASE_TAG_FAMILY_PATTERNS` (release pruning), or old releases stop being recognized. Verify generated `dist/build-results.json`, checksums, release metadata, asset names, and cleanup behavior before opening a PR.

## Commit & Pull Request Guidelines

Recent history uses concise subjects such as `feat: bypass-router tuning ...`. Prefer an imperative, scoped subject with `feat:`, `fix:`, `docs:`, or `chore:`. CI no longer generates commits, so `[skip ci]` should not appear in new commits. PRs should describe the build or conversion impact, link related issues, list manual validation commands, and call out release artifact or workflow changes.

## Security & Configuration Tips

Treat this repository as public source. Do not commit firmware images, runtime secrets, GitHub tokens, or injected OpenWrt configuration containing credentials. Official ImmortalWrt packages are mandatory; do not add unsigned third-party APK feeds or fallback download paths. Treat ImageBuilder URLs, checksums, `config/build-profile.json`, and pinned GitHub Action SHAs as security-sensitive changes. Build jobs remain read-only, and only the isolated GitHub-hosted publish job may receive `contents: write` — solely to create and prune Releases, never to push commits.
