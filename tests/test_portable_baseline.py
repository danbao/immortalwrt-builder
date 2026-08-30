import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = REPOSITORY_ROOT / "files/etc/uci-defaults/99-bypass-router.sh"
SETUP_WIZARD = REPOSITORY_ROOT / "scripts/setup-openwrt.sh"


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


if __name__ == "__main__":
    unittest.main()
