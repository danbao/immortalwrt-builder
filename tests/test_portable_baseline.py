import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = REPOSITORY_ROOT / "files/etc/uci-defaults/99-bypass-router.sh"
SETUP_WIZARD = REPOSITORY_ROOT / "scripts/setup-openwrt.sh"
MAINTENANCE_SCRIPT = REPOSITORY_ROOT / "files/usr/libexec/daed-maintenance"
MAINTENANCE_MIGRATION = REPOSITORY_ROOT / "files/etc/uci-defaults/98-daed-maintenance.sh"


class PortableBaselineTests(unittest.TestCase):
    def _write_fake_uci(self, path: Path) -> None:
        path.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                state="${FAKE_UCI_STATE:?}"
                [ "${1:-}" != "-q" ] || shift
                command="$1"
                shift
                case "$command" in
                  get)
                    key="$1"
                    awk -F '\\t' -v key="$key" '$1 == key { print substr($0, length($1) + 2); found=1 } END { exit !found }' "$state"
                    ;;
                  set)
                    assignment="$1"; key="${assignment%%=*}"; value="${assignment#*=}"
                    tmp="${state}.tmp"
                    awk -F '\\t' -v key="$key" '$1 != key' "$state" > "$tmp"
                    printf '%s\\t%s\\n' "$key" "$value" >> "$tmp"
                    mv "$tmp" "$state"
                    ;;
                  add_list)
                    assignment="$1"; key="${assignment%%=*}"; value="${assignment#*=}"
                    current=$("$0" get "$key" 2>/dev/null || true)
                    "$0" set "$key=${current:+$current }$value"
                    ;;
                  delete)
                    key="$1"; tmp="${state}.tmp"
                    awk -F '\\t' -v key="$key" '$1 != key && index($1, key ".") != 1' "$state" > "$tmp"
                    mv "$tmp" "$state"
                    ;;
                  commit) : ;;
                  *) exit 2 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _run_migration(self, initial_state: dict[str, str]) -> tuple[dict[str, str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        self._write_fake_uci(fake_bin / "uci")
        state_path = root / "uci.state"
        state_path.write_text(
            "".join(f"{key}\t{value}\n" for key, value in sorted(initial_state.items())),
            encoding="utf-8",
        )
        for config in {key.split(".", 1)[0] for key in initial_state} | {
            "dhcp", "firewall", "irqbalance", "system", "luci", "network", "daed"
        }:
            config_path = root / "etc/config" / config
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(f"fixture:{config}\n", encoding="utf-8")
        script_path = root / "migration.sh"
        script_path.write_text(
            MIGRATION_SCRIPT.read_text(encoding="utf-8").replace("/etc/", f"{root}/etc/"),
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_UCI_STATE": str(state_path),
        }
        subprocess.run(["sh", str(script_path)], check=True, env=env)
        state = dict(
            line.split("\t", 1)
            for line in state_path.read_text(encoding="utf-8").splitlines()
            if line
        )
        return state, root

    def test_first_run_applies_portable_defaults_and_second_run_is_noop(self) -> None:
        state, root = self._run_migration({"system.@system[0].hostname": "ImmortalWrt"})
        self.assertEqual(state["dhcp.lan.ignore"], "1")
        self.assertEqual(state["firewall.tailscale.device"], "tailscale0")
        self.assertEqual(state["firewall.tailscale.input"], "REJECT")
        self.assertEqual(state["daed.config.enabled"], "0")
        self.assertEqual(state["network.globals.packet_steering"], "1")
        marker = root / "etc/openwrt-setup/migrations/2026-08-portable-baseline-v1"
        self.assertTrue(marker.is_file())
        before = (root / "uci.state").read_bytes()
        subprocess.run(
            ["sh", str(root / "migration.sh")],
            check=True,
            env=os.environ
            | {
                "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                "FAKE_UCI_STATE": str(root / "uci.state"),
            },
        )
        self.assertEqual(before, (root / "uci.state").read_bytes())

    def test_existing_site_values_survive_except_explicit_safety_migration(self) -> None:
        state, root = self._run_migration(
            {
                "dhcp.lan.ignore": "0",
                "network.lan.ipaddr": "10.10.0.2",
                "system.@system[0].hostname": "site-router",
                "system.@system[0].zonename": "Custom/Zone",
                "system.ntp.server": "site.ntp.example",
                "daed.config.enabled": "1",
                "daed.config.listen_addr": "127.0.0.1:2023",
                "firewall.tailscale.network": "tailscale tailscale",
            }
        )
        self.assertEqual(state["dhcp.lan.ignore"], "0")
        self.assertEqual(state["network.lan.ipaddr"], "10.10.0.2")
        self.assertEqual(state["system.@system[0].hostname"], "site-router")
        self.assertEqual(state["system.@system[0].zonename"], "Custom/Zone")
        self.assertEqual(state["system.ntp.server"], "site.ntp.example")
        self.assertEqual(state["daed.config.enabled"], "1")
        self.assertEqual(state["daed.config.listen_addr"], "127.0.0.1:2023")
        self.assertNotIn("firewall.tailscale.network", state)
        self.assertTrue((root / "etc/openwrt-setup/backups/2026-08-portable-baseline-v1/firewall").is_file())

    def test_setup_wizard_is_syntactically_valid_and_keeps_private_values_ephemeral(self) -> None:
        subprocess.run(["bash", "-n", str(SETUP_WIZARD)], check=True)
        payload = SETUP_WIZARD.read_text(encoding="utf-8")
        self.assertIn("ask_secret DAED_PASSWORD", payload)
        self.assertIn("ask_secret DAED_SUBSCRIPTION", payload)
        self.assertNotIn("write_env DAED_PASSWORD", payload)
        self.assertNotIn("write_env DAED_SUBSCRIPTION", payload)
        self.assertIn("pname(tailscaled) -> must_direct", payload)
        self.assertIn("sport(41641) -> direct", payload)
        self.assertIn('policy:min_moving_avg', payload)
        self.assertIn('nodes{edges{id name}}', payload)
        self.assertIn('run(dry:true)', payload)
        self.assertIn('run(dry:false)', payload)
        self.assertIn('sniffingTimeout:"50ms"', payload)
        self.assertIn("qname(suffix: tailscale.com) -> alidns", payload)
        self.assertIn("dip(224.0.0.0/3, 'ff00::/8') -> direct", payload)
        self.assertIn("bandwidthMaxTx", payload)
        self.assertIn("bandwidthMaxRx", payload)
        self.assertIn('__type(name:\\"globalInput\\")', payload)
        self.assertIn('inputFields{name}', payload)
        self.assertIn("'$current * $managed'", payload)
        self.assertIn('daed-maintenance restore "$daed_backup"', payload)

    def test_daed_global_merge_preserves_bandwidth_and_future_fields(self) -> None:
        current = {
            "bandwidthMaxTx": "200 mbps",
            "bandwidthMaxRx": "1 gbps",
            "futureCompatibilityField": "keep-me",
            "sniffingTimeout": "100ms",
        }
        managed = {"sniffingTimeout": "50ms", "logLevel": "warn"}
        result = subprocess.run(
            [
                "jq", "-cn",
                "--argjson", "current", json.dumps(current),
                "--argjson", "managed", json.dumps(managed),
                "$current * $managed",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        merged = json.loads(result.stdout)
        self.assertEqual(merged["bandwidthMaxTx"], "200 mbps")
        self.assertEqual(merged["bandwidthMaxRx"], "1 gbps")
        self.assertEqual(merged["futureCompatibilityField"], "keep-me")
        self.assertEqual(merged["sniffingTimeout"], "50ms")

    def test_daed_maintenance_overlay_is_busybox_compatible_and_persistent(self) -> None:
        subprocess.run(["sh", "-n", str(MAINTENANCE_SCRIPT)], check=True)
        subprocess.run(["sh", "-n", str(MAINTENANCE_MIGRATION)], check=True)
        maintenance = MAINTENANCE_SCRIPT.read_text(encoding="utf-8")
        migration = MAINTENANCE_MIGRATION.read_text(encoding="utf-8")
        sysupgrade = (REPOSITORY_ROOT / "files/etc/sysupgrade.conf").read_text(encoding="utf-8")
        self.assertIn("PRAGMA quick_check", maintenance)
        self.assertIn("healthCheck", maintenance)
        self.assertIn("flock", maintenance)
        self.assertIn("*/10 * * * * /usr/libexec/daed-maintenance health", migration)
        self.assertIn("17 4 * * * /usr/libexec/daed-maintenance backup", migration)
        self.assertIn("/etc/daed/backups/", sysupgrade)

    def test_daed_maintenance_migration_preserves_existing_cron_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            crontab = root / "etc/crontabs/root"
            crontab.parent.mkdir(parents=True)
            crontab.write_text("5 1 * * * /usr/bin/custom-job\n", encoding="utf-8")
            sysupgrade = root / "etc/sysupgrade.conf"
            sysupgrade.parent.mkdir(parents=True, exist_ok=True)
            sysupgrade.write_text("/etc/daed/wing.db\n", encoding="utf-8")
            cron_init = root / "etc/init.d/cron"
            cron_init.parent.mkdir(parents=True)
            cron_init.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cron_init.chmod(0o755)
            migration = root / "migration.sh"
            migration.write_text(
                MAINTENANCE_MIGRATION.read_text(encoding="utf-8").replace(
                    "/etc/", f"{root}/etc/"
                ),
                encoding="utf-8",
            )
            migration.chmod(0o755)

            subprocess.run(["sh", str(migration)], check=True)
            first = crontab.read_text(encoding="utf-8")
            subprocess.run(["sh", str(migration)], check=True)
            second = crontab.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertIn("/usr/bin/custom-job", second)
            self.assertEqual(second.count("daed-maintenance health"), 1)
            self.assertEqual(second.count("daed-maintenance backup"), 1)
            self.assertTrue(
                (root / "etc/openwrt-setup/migrations/2026-08-daed-maintenance-v1").is_file()
            )
            self.assertTrue((root / "etc/openwrt-setup/daed-observation.state").is_file())


if __name__ == "__main__":
    unittest.main()
