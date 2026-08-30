#!/bin/sh

set -eu

MIGRATION_ID="2026-08-daed-maintenance-v1"
MIGRATION_DIR="/etc/openwrt-setup/migrations"
MIGRATION_MARKER="${MIGRATION_DIR}/${MIGRATION_ID}"
CRONTAB="/etc/crontabs/root"
HEALTH_CRON="*/10 * * * * /usr/libexec/daed-maintenance health"
BACKUP_CRON="17 4 * * * /usr/libexec/daed-maintenance backup"
OBSERVATION_STATE="/etc/openwrt-setup/daed-observation.state"

if [ -e "$MIGRATION_MARKER" ]; then
	exit 0
fi

mkdir -p "$MIGRATION_DIR" "/etc/daed/backups"
chmod 700 "/etc/daed/backups"
touch "$CRONTAB"

backup_dir="/etc/openwrt-setup/backups/${MIGRATION_ID}"
mkdir -p "$backup_dir"
if [ ! -e "$backup_dir/root-crontab" ]; then
	cp -p "$CRONTAB" "$backup_dir/root-crontab"
	chmod 600 "$backup_dir/root-crontab"
fi

grep -qxF "$HEALTH_CRON" "$CRONTAB" || printf '%s\n' "$HEALTH_CRON" >> "$CRONTAB"
grep -qxF "$BACKUP_CRON" "$CRONTAB" || printf '%s\n' "$BACKUP_CRON" >> "$CRONTAB"

grep -qxF '/etc/daed/backups/' "/etc/sysupgrade.conf" || printf '%s\n' '/etc/daed/backups/' >> "/etc/sysupgrade.conf"

if [ ! -e "$OBSERVATION_STATE" ]; then
	set -- $(awk '
		/closed network connection/ { closed++ }
		/database is locked|SQLITE_BUSY/ { busy++ }
		END { printf "%d %d", closed, busy }
	' "/var/log/daed/daed.log" 2>/dev/null || printf '0 0')
	if [ -f "/var/log/daed/daed.log" ]; then
		log_lines="$(wc -l < "/var/log/daed/daed.log")"
	else
		log_lines=0
	fi
	printf '%s\n%s\n%s\n%s\n' "$(date +%s)" "${1:-0}" "${2:-0}" "$log_lines" > "$OBSERVATION_STATE"
	chmod 600 "$OBSERVATION_STATE"
fi

"/etc/init.d/cron" enable
"/etc/init.d/cron" reload >/dev/null 2>&1 || "/etc/init.d/cron" restart

touch "$MIGRATION_MARKER"
chmod 600 "$MIGRATION_MARKER"

exit 0
