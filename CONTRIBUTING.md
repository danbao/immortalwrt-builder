# Contributing

Keep changes focused. Do not commit firmware images, build directories, credentials, subscriptions, VPN configuration, private addresses, or generated Release metadata.

The firmware must remain an ImmortalWrt 25.12.1 `x86/64` ImageBuilder `generic` profile build. The package set lives in `config/build-profile.json` and must resolve entirely from the official signed ImmortalWrt feeds; do not add unsigned third-party APK feeds or fallback download paths. The `files/` overlay carries only generic bypass-router tuning and must never inject credentials.

Bump `BUILDER_VERSION` in `scripts/openwrt_img_to_ova.py` whenever conversion logic or release tag semantics change; it records which builder produced an artifact. If you change the release tag format, update `published_tag_pattern` in the same file and `RELEASE_TAG_FAMILY_PATTERNS` in `scripts/publish_releases.py`, otherwise dedup and release pruning stop recognizing existing releases.

Before opening a pull request, run:

```sh
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
bash -n scripts/setup-openwrt.sh
shellcheck --exclude=SC2016,SC2029,SC2034 scripts/setup-openwrt.sh files/etc/uci-defaults/*.sh
```

Python code uses the standard library, four-space indentation, type hints, `Path`, small helpers, and explicit subprocess argument lists. Add behavior-focused tests for every new validation or failure mode.
