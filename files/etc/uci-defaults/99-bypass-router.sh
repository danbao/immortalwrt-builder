#!/bin/sh

INIT_DIR="${BYPASS_INIT_DIR:-/etc/init.d}"
CRONTAB="${BYPASS_CRONTAB:-/etc/crontabs/root}"

# Prepare the two-link bridge. Site-specific addresses are applied from the VM
# console with bypass-router-configure and are never baked into the image.
uci -q set network.lan.device='br-lan'

bridge_section="$(uci -q show network | sed -n "s/^\(network\.[^.]*\)\.name='br-lan'$/\1/p" | head -n 1)"
if [ -z "$bridge_section" ]; then
	uci -q set network.br_lan='device'
	uci -q set network.br_lan.name='br-lan'
	uci -q set network.br_lan.type='bridge'
	bridge_section='network.br_lan'
fi
uci -q set "${bridge_section}.stp=1"
uci -q delete "${bridge_section}.ports"
uci -q add_list "${bridge_section}.ports=eth0"
uci -q add_list "${bridge_section}.ports=eth1"
uci -q commit network

# Bypass router: the main router owns address assignment.
uci -q set dhcp.lan.ignore='1'
uci -q set dhcp.lan.ra='disabled'
uci -q set dhcp.lan.dhcpv6='disabled'
uci -q commit dhcp

# Flow offload stays disabled by default because transparent proxy rules depend
# on nftables prerouting/forward hooks. Keep HW offload disabled for virtual NICs.
uci -q set firewall.@defaults[0].flow_offloading='0'
uci -q set firewall.@defaults[0].flow_offloading_hw='0'

uci -q commit firewall

# Public DNS defaults. Site addresses and credentials remain runtime-only.
if uci -q get mosdns.config >/dev/null; then
	uci -q set mosdns.config.enabled='1'
	uci -q set mosdns.config.listen_address='127.0.0.1'
	uci -q set mosdns.config.listen_port='5335'
	uci -q set mosdns.config.listen_port_api='9091'
	uci -q set mosdns.config.custom_local_dns='1'
	uci -q delete mosdns.config.local_dns
	uci -q add_list mosdns.config.local_dns='223.5.5.5'
	uci -q add_list mosdns.config.local_dns='119.29.29.29'
	uci -q delete mosdns.config.remote_dns
	uci -q add_list mosdns.config.remote_dns='https://dns.google/dns-query'
	uci -q add_list mosdns.config.remote_dns='https://dns11.quad9.net/dns-query'
	uci -q set mosdns.config.bootstrap_dns='119.29.29.29'
	uci -q set mosdns.config.cache='1'
	uci -q set mosdns.config.cache_size='8000'
	uci -q set mosdns.config.lazy_cache_ttl='86400'
	uci -q set mosdns.config.prefer_ipv4='1'
	uci -q set mosdns.config.insecure_skip_verify='0'
	uci -q set mosdns.config.redirect='1'
	# A single verified updater is installed below; disable MosDNS' duplicate cron.
	uci -q set mosdns.config.geo_auto_update='0'
	uci -q commit mosdns

	uci -q delete 'dhcp.@dnsmasq[0].server'
	uci -q add_list 'dhcp.@dnsmasq[0].server=127.0.0.1#5335'
	uci -q commit dhcp

	mkdir -p "$(dirname "$CRONTAB")"
	cron_tmp="$(mktemp "${CRONTAB}.XXXXXX")" || exit 1
	if [ -f "$CRONTAB" ]; then
		sed '/\/usr\/share\/mosdns\/mosdns\.uc update/d;/mosdns-geo-update-verified/d' \
			"$CRONTAB" > "$cron_tmp"
	fi
	printf '%s\n' '0 3 * * 0 /usr/local/sbin/mosdns-geo-update-verified >/tmp/mosdns-geo-update.log 2>&1 # verified MosDNS geodata' \
		>> "$cron_tmp"
	mv "$cron_tmp" "$CRONTAB"
fi

for service in mosdns dnsmasq daed; do
	if [ -x "${INIT_DIR}/${service}" ]; then
		"${INIT_DIR}/${service}" enable
	fi
done

# Ensure irqbalance auto-starts (package is preinstalled) to spread softirq/NIC
# interrupts across CPU cores.
if ! uci -q get irqbalance.irqbalance >/dev/null; then
	uci -q set irqbalance.irqbalance='irqbalance'
fi
uci -q set irqbalance.irqbalance.enabled='1'
uci -q commit irqbalance

uci -q set system.@system[0].hostname='immortalwrt-bypass'
uci -q set system.@system[0].zonename='Asia/Shanghai'
uci -q set system.@system[0].timezone='CST-8'
uci -q set system.@system[0].log_size='1024'
if ! uci -q get system.ntp >/dev/null; then
	uci -q set system.ntp='timeserver'
fi
uci -q set system.ntp.enabled='1'
uci -q set system.ntp.enable_server='0'
uci -q delete system.ntp.server
uci -q add_list system.ntp.server='ntp.aliyun.com'
uci -q add_list system.ntp.server='ntp.tencent.com'
uci -q add_list system.ntp.server='cn.pool.ntp.org'
uci -q add_list system.ntp.server='pool.ntp.org'
uci -q commit system

if ! uci -q get luci.main >/dev/null; then
	uci -q set luci.main='core'
fi
uci -q set luci.main.mediaurlbase='/luci-static/argon'
uci -q commit luci

# Enable HTTPS immediately. Address binding is applied by the console-only
# site configuration step.
if uci -q get uhttpd.main >/dev/null; then
	uci -q set uhttpd.main.redirect_https='1'
	uci -q commit uhttpd
fi

# Avoid probing the BIOS boot-reserved partition as an anonymous filesystem.
if uci -q get fstab.@global[0] >/dev/null; then
	uci -q set fstab.@global[0].anon_mount='0'
	uci -q commit fstab
fi

# These services are deliberately absent from the package profile. Disable any
# copy inherited from a future base image as a defense-in-depth measure.
for service in zerotier miniupnpd tailscale; do
	if [ -x "${INIT_DIR}/${service}" ]; then
		"${INIT_DIR}/${service}" disable
		"${INIT_DIR}/${service}" stop >/dev/null 2>&1 || true
	fi
done

if [ -x "${INIT_DIR}/bypass-router-hardening" ]; then
	"${INIT_DIR}/bypass-router-hardening" enable
fi

updater_cron="30 4 * * * '/usr/sbin/immortalwrt-updater' check --refresh >'/tmp/immortalwrt-updater-cron.log' 2>&1"
mkdir -p "$(dirname "$CRONTAB")"
grep -Fqx "$updater_cron" "$CRONTAB" 2>/dev/null || printf '%s\n' "$updater_cron" >> "$CRONTAB"

if ! uci -q get network.globals >/dev/null; then
	uci -q set network.globals='globals'
fi
uci -q set network.globals.packet_steering='1'
uci -q commit network

exit 0
