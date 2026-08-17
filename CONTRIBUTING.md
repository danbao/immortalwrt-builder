# Contributing

Keep changes focused. Do not commit firmware images, build directories, credentials, subscriptions, VPN configuration, private addresses, or generated Release metadata.

The firmware must remain an ImmortalWrt 25.12.1 `x86/generic` ImageBuilder `generic` profile build with only `luci-app-vlmcsd` added through `PACKAGES`. Do not add other custom packages, third-party package downloads, or a `FILES` overlay without an explicit project decision.

Before opening a pull request, run:

```sh
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

Python code uses the standard library, four-space indentation, type hints, `Path`, small helpers, and explicit subprocess argument lists. Add behavior-focused tests for every new validation or failure mode.
