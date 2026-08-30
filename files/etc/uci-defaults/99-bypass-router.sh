#!/bin/sh

set -eu

MIGRATION_ID="2026-08-portable-baseline-v1"
MIGRATION_DIR="/etc/openwrt-setup/migrations"
MIGRATION_MARKER="${MIGRATION_DIR}/${MIGRATION_ID}"

if [ -e "${MIGRATION_MARKER}" ]; then
	exit 0
fi

mkdir -p "${MIGRATION_DIR}"

uci_set_if_missing() {
	config="$1"
	section="$2"
	option="$3"
	value="$4"

	if ! uci -q get "${config}.${section}.${option}" >/dev/null; then
		uci -q set "${config}.${section}.${option}=${value}"
	fi
}

ensure_section() {
	config="$1"
	section="$2"
	type="$3"

	if ! uci -q get "${config}.${section}" >/dev/null; then
		uci -q set "${config}.${section}=${type}"
	fi
}

backup_config_once() {
	config="$1"
	backup_dir="/etc/openwrt-setup/backups/${MIGRATION_ID}"
	backup_path="${backup_dir}/${config}"

	if [ -f "/etc/config/${config}" ] && [ ! -e "${backup_path}" ]; then
		mkdir -p "${backup_dir}"
		cp -p "/etc/config/${config}" "${backup_path}"
		chmod 600 "${backup_path}"
	fi
}

# The main router owns address assignment. Existing values always win.
backup_config_once dhcp
ensure_section dhcp lan dhcp
uci_set_if_missing dhcp lan ignore 1
uci_set_if_missing dhcp lan ra disabled
uci_set_if_missing dhcp lan dhcpv6 disabled
uci -q commit dhcp

# Transparent proxy hooks must not be bypassed by flow offload.
backup_config_once firewall
uci_set_if_missing firewall '@defaults[0]' flow_offloading 0
uci_set_if_missing firewall '@defaults[0]' flow_offloading_hw 0

# Tailscale is matched by device. A netifd proto=none interface can delete
# routes installed by tailscaled, so remove only that known-bad shape.
if [ "$(uci -q get network.tailscale.proto || true)" = "none" ] && \
	[ "$(uci -q get network.tailscale.device || true)" = "tailscale0" ]; then
	backup_config_once network
	uci -q delete network.tailscale
	uci -q commit network
fi

ensure_section firewall tailscale zone
if uci -q get firewall.tailscale.network >/dev/null; then
	backup_config_once firewall
	uci -q delete firewall.tailscale.network
fi
uci -q set firewall.tailscale.name='tailscale'
uci -q set firewall.tailscale.input='REJECT'
uci -q set firewall.tailscale.output='ACCEPT'
uci -q set firewall.tailscale.forward='REJECT'
uci -q set firewall.tailscale.masq='0'
uci -q set firewall.tailscale.device='tailscale0'

ensure_section firewall tailscale_to_lan forwarding
uci -q set firewall.tailscale_to_lan.src='tailscale'
uci -q set firewall.tailscale_to_lan.dest='lan'

ensure_section firewall allow_tailscale_icmp rule
uci -q set firewall.allow_tailscale_icmp.name='Allow-Tailscale-ICMP'
uci -q set firewall.allow_tailscale_icmp.src='tailscale'
uci -q set firewall.allow_tailscale_icmp.proto='icmp'
uci -q set firewall.allow_tailscale_icmp.family='ipv4'
uci -q set firewall.allow_tailscale_icmp.target='ACCEPT'

ensure_section firewall allow_tailscale_mgmt rule
uci -q set firewall.allow_tailscale_mgmt.name='Allow-Tailscale-Management'
uci -q set firewall.allow_tailscale_mgmt.src='tailscale'
uci -q set firewall.allow_tailscale_mgmt.proto='tcp'
uci -q set firewall.allow_tailscale_mgmt.dest_port='22 80 443 2023'
uci -q set firewall.allow_tailscale_mgmt.target='ACCEPT'
uci -q commit firewall

# irqbalance, locale, LuCI and packet steering use conservative defaults.
backup_config_once irqbalance
ensure_section irqbalance irqbalance irqbalance
uci_set_if_missing irqbalance irqbalance enabled 1
uci -q commit irqbalance

if [ "$(uci -q get system.@system[0].hostname || true)" = "ImmortalWrt" ]; then
	backup_config_once system
	uci -q set system.@system[0].hostname='immortalwrt-bypass'
fi
backup_config_once system
uci_set_if_missing system '@system[0]' zonename Asia/Shanghai
uci_set_if_missing system '@system[0]' timezone CST-8
ensure_section system ntp timeserver
uci_set_if_missing system ntp enabled 1
uci_set_if_missing system ntp enable_server 0
if ! uci -q get system.ntp.server >/dev/null; then
	uci -q add_list system.ntp.server='ntp.aliyun.com'
	uci -q add_list system.ntp.server='ntp.tencent.com'
	uci -q add_list system.ntp.server='cn.pool.ntp.org'
	uci -q add_list system.ntp.server='pool.ntp.org'
fi
uci -q commit system

backup_config_once luci
ensure_section luci main core
uci_set_if_missing luci main mediaurlbase /luci-static/argon
uci -q commit luci

backup_config_once network
ensure_section network globals globals
uci_set_if_missing network globals packet_steering 1
uci -q commit network

# daed remains disabled until the setup wizard creates its administrator and
# subscription. Existing runtime settings are never overwritten.
backup_config_once daed
ensure_section daed config daed
uci_set_if_missing daed config enabled 0
uci_set_if_missing daed config listen_addr 0.0.0.0:2023
uci_set_if_missing daed config log_maxbackups 1
uci_set_if_missing daed config log_maxsize 5
uci -q commit daed

# Prepare package-owned services without creating identities or credentials.
if [ -x /etc/init.d/vmtoolsd ]; then
	/etc/init.d/vmtoolsd enable
fi
if [ -x /etc/init.d/vnstat ]; then
	mkdir -p /etc/vnstat
	/etc/init.d/vnstat enable
fi
if [ -s /etc/tailscale/tailscaled.state ] && [ -x /etc/init.d/tailscale ]; then
	/etc/init.d/tailscale enable
fi

touch "${MIGRATION_MARKER}"
chmod 600 "${MIGRATION_MARKER}"

exit 0
