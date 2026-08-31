# Repository Guidelines

## Project Structure & Module Organization

This repository automates building ImmortalWrt 25.12.1 x86_64 images, converting them to ESXi-ready OVA files, and publishing release artifacts. Core automation lives in `scripts/`: `openwrt_build_preflight.py` resolves/downloads the ImageBuilder and validates the centralized build profile, ImageBuilder security options, and official package manifest; `openwrt_img_to_ova.py` handles image discovery, conversion, release assets, provenance metadata, checksums, and manifest recording; `publish_releases.py` validates and creates GitHub Releases from explicitly declared assets; `update_release_records.py` regenerates and pushes release records with bounded retries. The GitHub Actions entrypoint is `.github/workflows/build-openwrt.yml`, and the single build profile is `config/build-profile.json`. Generated release records are stored in `manifests/converted-images.json` and rendered to `docs/converted-images.md`. Build outputs such as `dist/`, `build-out/`, `imagebuilder/`, `*.ova`, and `*.vmdk` must stay out of git.

## Build, Test, and Development Commands

- `sudo apt-get install -y qemu-utils`: installs `qemu-img`, required for local conversion.
- `python3 scripts/openwrt_build_preflight.py validate-profile --config config/build-profile.json`: validates the centralized single build profile and prints workflow values.
- `python3 scripts/openwrt_build_preflight.py validate-imagebuilder --profile config/build-profile.json --config imagebuilder/.config`: asserts the required signature, TLS, manifest, and CycloneDX SBOM options.
- `python3 scripts/openwrt_build_preflight.py validate-manifest --profile config/build-profile.json --manifest build-out/official.packages.manifest --metadata-out build-out/official-packages.json`: validates the official daed package set and records versions.
- `python3 scripts/openwrt_img_to_ova.py scan --img-dir <dir-with-img-files> --manifest manifests/converted-images.json --out-dir dist --results dist/build-results.json --nic-count 1`: converts unrecorded `.img` or `.img.gz` files into OVA artifacts. CI also passes `--release-date`, `--immortalwrt-version-code`, and `--immortalwrt-commit`.
- `python3 scripts/openwrt_img_to_ova.py record --results dist/build-results.json --manifest manifests/converted-images.json --doc docs/converted-images.md`: records successfully published conversions.
- `python3 scripts/publish_releases.py dist/build-results.json --keep-releases 30 --expected-repository-commit <commit> --expected-workflow-run-url <url>`: validates the release handoff against trusted run provenance, publishes all declared assets, and prunes older managed releases; requires authenticated `gh`.
- `python3 -m py_compile scripts/*.py`: quick syntax check for script-only changes.
- `python3 -m unittest discover -s tests`: runs the unit tests.

## Coding Style & Naming Conventions

Use Python 3 standard library only unless a new dependency is justified. Follow the existing style: 4-space indentation, type hints, `Path` for filesystem paths, small pure helpers, and explicit subprocess argument lists. Keep CLI flags kebab-cased, generated filenames lowercase and descriptive, and manifest fields stable because CI and release publishing consume them.

## Testing Guidelines

Run `python3 -m unittest discover -s tests` for script changes; network-dependent commands are tested by mocking `fetch_bytes`/`download_url`. For conversion logic, release tags, or asset names, bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` so prior manifest entries are not reused incorrectly. Verify generated `dist/build-results.json`, checksums, release metadata, asset names, cleanup behavior, and `docs/converted-images.md` before opening a PR.

## Commit & Pull Request Guidelines

Recent history uses concise subjects such as `feat: bypass-router tuning ...` and `docs: record converted OpenWrt images [skip ci]`. Prefer an imperative, scoped subject with `feat:`, `fix:`, `docs:`, or `chore:`. Reserve `[skip ci]` for generated manifest/docs record commits. PRs should describe the build or conversion impact, link related issues, list manual validation commands, and call out release artifact or workflow changes.

## Security & Configuration Tips

Treat this repository as public source. Do not commit firmware images, runtime secrets, GitHub tokens, or injected OpenWrt configuration containing credentials. Official ImmortalWrt packages are mandatory; do not add unsigned third-party APK feeds or fallback download paths. Treat ImageBuilder URLs, checksums, `config/build-profile.json`, and pinned GitHub Action SHAs as security-sensitive changes. Build jobs remain read-only, and only the isolated GitHub-hosted publish job may receive `contents: write`.
