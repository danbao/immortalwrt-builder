# Contributing

Use a focused branch and keep changes small. Do not commit firmware, build directories, credentials, subscriptions, VPN configuration, private addresses, or generated Release metadata.

Before opening a pull request, run:

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
shellcheck files/etc/uci-defaults/99-bypass-router.sh \
  files/usr/sbin/bypass-router-configure \
  files/usr/sbin/bypass-router-cutover \
  files/usr/sbin/bypass-router-harden \
  files/usr/share/luci-app-daede/daed-filter-sync.sh
```

Changes to download sources, package lists, signature handling, GitHub Actions permissions, Release naming, OVF generation, or cleanup behavior are security-sensitive. Explain the source and risk impact in the pull request.

Python code uses the standard library, four-space indentation, type hints, `Path`, small helpers, and explicit subprocess argument lists. Add behavior-focused tests for every new validation or failure mode.
