#!/bin/sh

set -eu

UCODE_REPOSITORY='https://github.com/jow-/ucode.git'
UCODE_COMMIT='85922056ef7abeace3cca3ab28bc1ac2d88e31b1'

fail() {
	printf 'ERROR: %s\n' "$*" >&2
	exit 1
}

[ "$#" -eq 1 ] || fail "usage: $0 ABSOLUTE_INSTALL_PREFIX"
install_prefix="$1"
case "$install_prefix" in
	/*) ;;
	*) fail 'install prefix must be absolute' ;;
esac

temporary_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
work_dir="$(mktemp -d "${temporary_root%/}/immortalwrt-host-ucode.XXXXXX")"
source_dir="${work_dir}/source"

git clone --filter=blob:none --no-checkout "$UCODE_REPOSITORY" "$source_dir"
git -C "$source_dir" checkout --detach "$UCODE_COMMIT"
actual_commit="$(git -C "$source_dir" rev-parse HEAD)"
[ "$actual_commit" = "$UCODE_COMMIT" ] || fail 'ucode checkout does not match the pinned commit'

cmake -S "$source_dir" -B "${source_dir}/build" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX="$install_prefix" \
	-DCMAKE_INSTALL_RPATH="${install_prefix}/lib" \
	-DUBUS_SUPPORT=OFF -DUCI_SUPPORT=OFF -DULOOP_SUPPORT=OFF \
	-DNL80211_SUPPORT=OFF -DRTNL_SUPPORT=OFF -DRESOLV_SUPPORT=OFF \
	-DLOG_SUPPORT=OFF -DDIGEST_SUPPORT=OFF
cmake --build "${source_dir}/build" --parallel 2
cmake --install "${source_dir}/build"

[ -x "${install_prefix}/bin/ucode" ] || fail 'ucode compiler was not installed'
