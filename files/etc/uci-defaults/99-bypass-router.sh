#!/bin/sh

# Bypass router: the main router owns address assignment.
uci -q set dhcp.lan.ignore='1'
uci -q set dhcp.lan.ra='disabled'
uci -q set dhcp.lan.dhcpv6='disabled'
uci -q commit dhcp

uci -q set system.@system[0].hostname='immortalwrt-bypass'
uci -q set system.@system[0].zonename='Asia/Shanghai'
uci -q set system.@system[0].timezone='CST-8'
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

if ! uci -q get network.globals >/dev/null; then
	uci -q set network.globals='globals'
fi
uci -q set network.globals.packet_steering='1'
uci -q commit network

exit 0
