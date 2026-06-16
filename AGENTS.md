# Repository Guidelines

## Project Structure & Module Organization

This repository automates building ImmortalWrt x86_64 images, converting them to ESXi-ready OVA files, and publishing release artifacts. Core automation lives in `scripts/`: `openwrt_img_to_ova.py` handles image discovery, conversion, release tag metadata, checksums, and manifest recording; `publish_releases.py` creates GitHub Releases from conversion results. The GitHub Actions entrypoint is `.github/workflows/build-openwrt.yml`. Generated release records are stored in `manifests/converted-images.json` and rendered to `docs/converted-images.md`. Build outputs such as `dist/`, `build-out/`, `imagebuilder/`, `*.ova`, and `*.vmdk` must stay out of git.

## Build, Test, and Development Commands

- `sudo apt-get install -y qemu-utils`: installs `qemu-img`, required for local conversion.
- `python3 scripts/openwrt_img_to_ova.py scan --img-dir <dir-with-img-files> --manifest manifests/converted-images.json --out-dir dist --results dist/build-results.json --nic-count 1`: converts unrecorded `.img` or `.img.gz` files into OVA artifacts. CI also passes `--release-date`, `--immortalwrt-version-code`, and `--immortalwrt-commit`.
- `python3 scripts/openwrt_img_to_ova.py record --results dist/build-results.json --manifest manifests/converted-images.json --doc docs/converted-images.md`: records successfully published conversions.
- `python3 scripts/publish_releases.py dist/build-results.json --keep-releases 30`: publishes built OVA/checksum artifacts and prunes older managed releases; requires authenticated `gh`.
- `python3 -m py_compile scripts/*.py`: quick syntax check for script-only changes.

## Coding Style & Naming Conventions

Use Python 3 standard library only unless a new dependency is justified. Follow the existing style: 4-space indentation, type hints, `Path` for filesystem paths, small pure helpers, and explicit subprocess argument lists. Keep CLI flags kebab-cased, generated filenames lowercase and descriptive, and manifest fields stable because CI and release publishing consume them.

## Testing Guidelines

There is no dedicated test suite. For Python changes, run `py_compile` and exercise the affected command with a small local image when practical. For conversion logic or release tag changes, bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` so prior manifest entries are not reused incorrectly. Verify generated `dist/build-results.json`, checksums, release tag metadata, release cleanup behavior, and `docs/converted-images.md` before opening a PR.

## Commit & Pull Request Guidelines

Recent history uses concise subjects such as `feat: bypass-router tuning ...` and `docs: record converted OpenWrt images [skip ci]`. Prefer an imperative, scoped subject with `feat:`, `fix:`, `docs:`, or `chore:`. Reserve `[skip ci]` for generated manifest/docs record commits. PRs should describe the build or conversion impact, link related issues, list manual validation commands, and call out release artifact or workflow changes.

## Security & Configuration Tips

Keep this repository private. Do not commit firmware images, runtime secrets, GitHub tokens, or injected OpenWrt configuration containing credentials. Treat third-party feed and package URL changes as security-sensitive and document why the source is trusted.
