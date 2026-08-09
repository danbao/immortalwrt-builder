import os
import hashlib
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "files" / "usr" / "sbin" / "bypass-router-configure"
CUTOVER = ROOT / "files" / "usr" / "sbin" / "bypass-router-cutover"
HARDEN = ROOT / "files" / "usr" / "sbin" / "bypass-router-harden"
FILTER_SYNC = ROOT / "files" / "usr" / "share" / "luci-app-daede" / "daed-filter-sync.sh"
UCI_DEFAULTS = ROOT / "files" / "etc" / "uci-defaults" / "99-bypass-router.sh"
GEO_UPDATE = ROOT / "files" / "usr" / "local" / "sbin" / "mosdns-geo-update-verified"
DAED_CONFIGURE = ROOT / "files" / "usr" / "sbin" / "bypass-router-daed-configure"
DAED_SYNC = ROOT / "files" / "usr" / "local" / "sbin" / "daed-subscription-sync"


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class RouterScriptTests(unittest.TestCase):
    def run_configure(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [CONFIGURE, *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_configure_rejects_invalid_address_before_touching_uci(self) -> None:
        result = self.run_configure(
            "not-an-ip",
            "192.0.2.2",
            "192.0.2.1",
            "192.0.2.0/24",
            "--confirm",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid staging IPv4 address", result.stderr)

    def test_configure_requires_confirmation_and_a_supported_subnet(self) -> None:
        missing_confirmation = self.run_configure()
        self.assertNotEqual(missing_confirmation.returncode, 0)
        self.assertIn("--confirm", missing_confirmation.stderr)

        wrong_prefix = self.run_configure(
            "192.0.2.19",
            "192.0.2.2",
            "192.0.2.1",
            "192.0.2.0/25",
            "--confirm",
        )
        self.assertNotEqual(wrong_prefix.returncode, 0)
        self.assertIn("only a /24", wrong_prefix.stderr)

        same_address = self.run_configure(
            "192.0.2.19",
            "192.0.2.19",
            "192.0.2.1",
            "192.0.2.0/24",
            "--confirm",
        )
        self.assertNotEqual(same_address.returncode, 0)
        self.assertIn("must differ", same_address.stderr)

    def test_configure_applies_site_values_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            init_dir = tmp / "init.d"
            bin_dir.mkdir()
            init_dir.mkdir()
            uci_log = tmp / "uci.log"
            init_log = tmp / "init.log"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UCI_LOG\"\nexit 0\n",
            )
            for service in ("firewall", "network", "uhttpd", "daed"):
                make_executable(
                    init_dir / service,
                    "#!/bin/sh\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$INIT_LOG\"\n",
                )
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "UCI_LOG": str(uci_log),
                "INIT_LOG": str(init_log),
                "BYPASS_INIT_DIR": str(init_dir),
            }
            result = subprocess.run(
                [
                    CONFIGURE,
                    "192.0.2.19",
                    "192.0.2.2",
                    "192.0.2.1",
                    "192.0.2.0/24",
                    "--confirm",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            uci_calls = uci_log.read_text(encoding="utf-8")
            self.assertIn("network.lan.ipaddr=192.0.2.19", uci_calls)
            self.assertIn("bypass_router.main.target_ip=192.0.2.2", uci_calls)
            self.assertIn("firewall.bypass_mgmt_allow.src_ip=192.0.2.0/24", uci_calls)
            self.assertIn("add_list uhttpd.main.listen_http=127.0.0.1:80", uci_calls)
            self.assertIn("add_list uhttpd.main.listen_https=127.0.0.1:443", uci_calls)
            self.assertIn("network reload", init_log.read_text(encoding="utf-8"))

    def test_configure_accepts_runtime_bridge_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            init_dir = tmp / "init.d"
            bin_dir.mkdir()
            init_dir.mkdir()
            uci_log = tmp / "uci.log"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UCI_LOG\"\nexit 0\n",
            )
            for service in ("firewall", "network", "uhttpd", "daed"):
                make_executable(init_dir / service, "#!/bin/sh\nexit 0\n")
            result = subprocess.run(
                [
                    CONFIGURE,
                    "192.0.2.19",
                    "192.0.2.2",
                    "192.0.2.1",
                    "192.0.2.0/24",
                    "--ports",
                    "eth0,eth1,eth2,eth3,eth4,eth5",
                    "--confirm",
                ],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "UCI_LOG": str(uci_log),
                    "BYPASS_INIT_DIR": str(init_dir),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = uci_log.read_text(encoding="utf-8")
            self.assertIn("delete network.br_lan.ports", calls)
            for port in ("eth0", "eth1", "eth2", "eth3", "eth4", "eth5"):
                self.assertIn(f"add_list network.br_lan.ports={port}", calls)

    def test_configure_rejects_unsafe_bridge_port_names(self) -> None:
        cases = (
            ("eth0,eth1;reboot", "invalid bridge port"),
            ("", "invalid bridge port list"),
            ("eth0", "at least two bridge ports"),
            (",eth0", "invalid bridge port list"),
            ("eth0,", "invalid bridge port list"),
            ("eth0,,eth1", "invalid bridge port list"),
        )
        for ports, expected in cases:
            with self.subTest(ports=ports):
                result = self.run_configure(
                    "192.0.2.19",
                    "192.0.2.2",
                    "192.0.2.1",
                    "192.0.2.0/24",
                    "--ports",
                    ports,
                    "--confirm",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_first_boot_sets_public_dns_defaults_and_disables_tailscale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            init_dir = tmp / "init.d"
            bin_dir.mkdir()
            init_dir.mkdir()
            uci_log = tmp / "uci.log"
            service_log = tmp / "services.log"
            crontab = tmp / "root.cron"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$UCI_LOG\"\n"
                "case \"$*\" in\n"
                "  *'show network'*) echo \"network.br_lan.name='br-lan'\" ;;\n"
                "  *'get daed.config'*) exit 1 ;;\n"
                "esac\n"
                "exit 0\n",
            )
            for service in (
                "mosdns",
                "dnsmasq",
                "daed",
                "tailscale",
                "zerotier",
                "miniupnpd",
            ):
                make_executable(
                    init_dir / service,
                    "#!/bin/sh\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$SERVICE_LOG\"\n",
                )
            result = subprocess.run(
                [UCI_DEFAULTS],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "UCI_LOG": str(uci_log),
                    "SERVICE_LOG": str(service_log),
                    "BYPASS_INIT_DIR": str(init_dir),
                    "BYPASS_CRONTAB": str(crontab),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = uci_log.read_text(encoding="utf-8")
            self.assertIn("set mosdns.config.listen_address=0.0.0.0", calls)
            self.assertIn("set mosdns.config.listen_port=5335", calls)
            self.assertIn("add_list mosdns.config.local_dns=223.5.5.5", calls)
            self.assertIn("add_list mosdns.config.local_dns=119.29.29.29", calls)
            self.assertIn("add_list mosdns.config.remote_dns=https://dns.google/dns-query", calls)
            self.assertIn("add_list mosdns.config.remote_dns=https://cloudflare-dns.com/dns-query", calls)
            self.assertNotIn("quad9", calls.lower())
            self.assertIn("add_list dhcp.@dnsmasq[0].server=127.0.0.1#5335", calls)
            self.assertIn("set mosdns.config.geo_auto_update=0", calls)
            self.assertEqual(
                crontab.read_text(encoding="utf-8").count("mosdns-geo-update-verified"),
                1,
            )
            services = service_log.read_text(encoding="utf-8")
            self.assertIn("mosdns enable", services)
            self.assertIn("dnsmasq enable", services)
            self.assertIn("daed enable", services)
            self.assertIn("tailscale disable", services)
            self.assertIn("tailscale stop", services)

    def make_geo_update_fixture(
        self, tmp: Path, corrupt_geosite: bool = False, fail_dump: bool = False
    ) -> dict[str, str]:
        bin_dir = tmp / "bin"
        v2ray_dir = tmp / "v2ray"
        runtime_dir = tmp / "runtime"
        bin_dir.mkdir()
        v2ray_dir.mkdir()
        runtime_dir.mkdir()
        (v2ray_dir / "geoip.dat").write_text("old-geoip", encoding="utf-8")
        (v2ray_dir / "geosite.dat").write_text("old-geosite", encoding="utf-8")
        geoip_digest = hashlib.sha256(b"new-geoip").hexdigest()
        geosite_digest = hashlib.sha256(b"new-geosite").hexdigest()
        make_executable(
            bin_dir / "curl",
            "#!/bin/sh\n"
            "output=''\nurl=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -o) output=$2; shift 2 ;;\n"
            "    -H|--connect-timeout|--max-time|--retry|--retry-delay) shift 2 ;;\n"
            "    -*) shift ;;\n"
            "    *) url=$1; shift ;;\n"
            "  esac\n"
            "done\n"
            "case \"$url\" in\n"
            "  *Loyalsoldier/geoip/releases/latest)\n"
            f"    printf '%s' '{{\"assets\":[{{\"name\":\"geoip-only-cn-private.dat\",\"id\":1,\"digest\":\"sha256:{geoip_digest}\"}}]}}' > \"$output\" ;;\n"
            "  *Loyalsoldier/v2ray-rules-dat/releases/latest)\n"
            f"    printf '%s' '{{\"assets\":[{{\"name\":\"geosite.dat\",\"id\":2,\"digest\":\"sha256:{geosite_digest}\"}}]}}' > \"$output\" ;;\n"
            "  */releases/assets/1) printf '%s' 'new-geoip' > \"$output\" ;;\n"
            + (
                "  */releases/assets/2) printf '%s' 'corrupt-geosite' > \"$output\" ;;\n"
                if corrupt_geosite
                else "  */releases/assets/2) printf '%s' 'new-geosite' > \"$output\" ;;\n"
            )
            + "  *) exit 1 ;;\n"
            "esac\n",
        )
        make_executable(
            bin_dir / "jsonfilter",
            "#!/bin/sh\n"
            "file=''\nexpression=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in -i) file=$2; shift 2 ;; -e) expression=$2; shift 2 ;; *) shift ;; esac\n"
            "done\n"
            "case \"$expression\" in\n"
            "  *'.id') sed -n 's/.*\"id\":\\([0-9][0-9]*\\).*/\\1/p' \"$file\" ;;\n"
            "  *'.digest') sed -n 's/.*\"digest\":\"\\([^\"]*\\)\".*/\\1/p' \"$file\" ;;\n"
            "esac\n",
        )
        make_executable(
            bin_dir / "asset-field",
            "#!/bin/sh\n"
            "case \"$3\" in\n"
            "  id) sed -n 's/.*\"id\":\\([0-9][0-9]*\\).*/\\1/p' \"$1\" ;;\n"
            "  digest) sed -n 's/.*\"digest\":\"\\([^\"]*\\)\".*/\\1/p' \"$1\" ;;\n"
            "esac\n",
        )
        make_executable(
            bin_dir / "mosdns-helper",
            "#!/bin/sh\n"
            "[ \"$1\" = v2dat_dump ] || exit 1\n"
            + ("exit 1\n" if fail_dump else "exit 0\n"),
        )
        make_executable(
            bin_dir / "mosdns-init",
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  running) exit 0 ;;\n"
            "  stop|start) echo \"$1\" >> \"$MOSDNS_INIT_LOG\" ;;\n"
            "esac\n",
        )
        return {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "MOSDNS_GEO_V2RAY_DIR": str(v2ray_dir),
            "MOSDNS_GEO_RUNTIME_DIR": str(runtime_dir),
            "MOSDNS_GEO_LOG": str(tmp / "update.log"),
            "MOSDNS_GEO_LOCK": str(tmp / "update.lock"),
            "MOSDNS_GEO_HELPER": str(bin_dir / "mosdns-helper"),
            "MOSDNS_GEO_INIT": str(bin_dir / "mosdns-init"),
            "MOSDNS_INIT_LOG": str(tmp / "mosdns-init.log"),
            "MOSDNS_GEO_ASSET_FIELD_HELPER": str(bin_dir / "asset-field"),
        }

    def test_verified_geo_update_replaces_both_datasets_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_geo_update_fixture(tmp)
            result = subprocess.run(
                [GEO_UPDATE], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((tmp / "v2ray" / "geoip.dat").read_text(), "new-geoip")
            self.assertEqual((tmp / "v2ray" / "geosite.dat").read_text(), "new-geosite")
            self.assertEqual(
                (tmp / "mosdns-init.log").read_text(encoding="utf-8").splitlines(),
                ["stop", "start"],
            )

    def test_verified_geo_update_keeps_old_datasets_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_geo_update_fixture(tmp, corrupt_geosite=True)
            result = subprocess.run(
                [GEO_UPDATE], env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((tmp / "v2ray" / "geoip.dat").read_text(), "old-geoip")
            self.assertEqual((tmp / "v2ray" / "geosite.dat").read_text(), "old-geosite")

    def test_verified_geo_update_rejects_release_assets_without_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_geo_update_fixture(tmp)
            make_executable(
                tmp / "bin" / "curl",
                "#!/bin/sh\n"
                "output=''\n"
                "while [ \"$#\" -gt 0 ]; do case \"$1\" in -o) output=$2; shift 2 ;; -H|--connect-timeout|--max-time|--retry|--retry-delay) shift 2 ;; -*) shift ;; *) shift ;; esac; done\n"
                "printf '%s' '{\"assets\":[{\"name\":\"geoip-only-cn-private.dat\",\"id\":1,\"digest\":\"\"}]}' > \"$output\"\n",
            )
            result = subprocess.run(
                [GEO_UPDATE], env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has no SHA256 digest", result.stderr)
            self.assertEqual((tmp / "v2ray" / "geoip.dat").read_text(), "old-geoip")
            self.assertEqual((tmp / "v2ray" / "geosite.dat").read_text(), "old-geosite")

    def test_verified_geo_update_rolls_back_when_export_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_geo_update_fixture(tmp, fail_dump=True)
            result = subprocess.run(
                [GEO_UPDATE], env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((tmp / "v2ray" / "geoip.dat").read_text(), "old-geoip")
            self.assertEqual((tmp / "v2ray" / "geosite.dat").read_text(), "old-geosite")

    def test_cutover_refuses_to_run_without_site_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            bin_dir = Path(tmp_s)
            make_executable(bin_dir / "uci", "#!/bin/sh\nexit 1\n")
            result = subprocess.run(
                [CUTOVER, "check"],
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("run bypass-router-configure first", result.stderr)

    def make_cutover_fixture(self, tmp: Path, carriers: tuple[str, ...]) -> dict[str, str]:
        bin_dir = tmp / "bin"
        sys_net = tmp / "sys-class-net"
        bridge_ports = sys_net / "br-lan" / "brif"
        bin_dir.mkdir()
        bridge_ports.mkdir(parents=True)
        for index, carrier in enumerate(carriers):
            interface = f"eth{index}"
            interface_dir = sys_net / interface
            (interface_dir / "statistics").mkdir(parents=True)
            (interface_dir / "carrier").write_text(carrier, encoding="utf-8")
            (interface_dir / "statistics" / "rx_bytes").write_text("1", encoding="utf-8")
            (interface_dir / "statistics" / "tx_bytes").write_text("1", encoding="utf-8")
            (bridge_ports / interface).symlink_to(interface_dir)
        make_executable(
            bin_dir / "uci",
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *bypass_router.main.target_ip*) echo 192.0.2.2 ;;\n"
            "  *network.lan.ipaddr*) echo 192.0.2.19 ;;\n"
            "esac\n",
        )
        return {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BYPASS_SYS_CLASS_NET": str(sys_net),
            "BYPASS_SAMPLE_SECONDS": "0",
        }

    def test_cutover_rejects_missing_port_carrier_and_traffic(self) -> None:
        cases = (
            (("1",), "needs two ports"),
            (("1", "0"), "has no carrier"),
            (("1", "1"), "carried no traffic"),
        )
        for carriers, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp_s:
                env = self.make_cutover_fixture(Path(tmp_s), carriers)
                result = subprocess.run(
                    [CUTOVER, "check"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_cutover_rejects_missing_proxy_process_after_healthy_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_cutover_fixture(tmp, ("1", "1"))
            make_executable(
                tmp / "bin" / "sleep",
                "#!/bin/sh\n"
                "for file in \"$BYPASS_SYS_CLASS_NET\"/eth*/statistics/rx_bytes; do\n"
                "  printf '2\\n' > \"$file\"\n"
                "done\n",
            )
            make_executable(tmp / "bin" / "pidof", "#!/bin/sh\nexit 1\n")
            result = subprocess.run(
                [CUTOVER, "check"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mosdns is not running", result.stderr)

    def test_cutover_rejects_an_occupied_target_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            env = self.make_cutover_fixture(tmp, ("1", "1"))
            make_executable(
                tmp / "bin" / "sleep",
                "#!/bin/sh\n"
                "for file in \"$BYPASS_SYS_CLASS_NET\"/eth*/statistics/rx_bytes; do\n"
                "  value=$(cat \"$file\")\n"
                "  printf '%s\\n' \"$((value + 1))\" > \"$file\"\n"
                "done\n",
            )
            make_executable(tmp / "bin" / "pidof", "#!/bin/sh\nexit 0\n")
            make_executable(tmp / "bin" / "ping", "#!/bin/sh\nexit 1\n")
            make_executable(
                tmp / "bin" / "ip",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *'neigh show'*) echo '192.0.2.2 dev br-lan lladdr REDACTED REACHABLE' ;;\n"
                "esac\n",
            )
            result = subprocess.run(
                [CUTOVER, "apply", "--confirm"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is still in use", result.stderr)

    def test_hardening_refuses_an_empty_authorized_keys_file(self) -> None:
        with tempfile.NamedTemporaryFile() as keys:
            result = subprocess.run(
                [HARDEN],
                env={**os.environ, "BYPASS_AUTHORIZED_KEYS": keys.name},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is empty", result.stderr)

    def test_hardening_rejects_comments_and_missing_dropbear_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            comments = tmp / "comments"
            comments.write_text("# no key here\n\n", encoding="utf-8")
            comments_result = subprocess.run(
                [HARDEN],
                env={**os.environ, "BYPASS_AUTHORIZED_KEYS": str(comments)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(comments_result.returncode, 0)
            self.assertIn("no usable SSH public key", comments_result.stderr)

            key = tmp / "authorized_keys"
            key.write_text("ssh-ed25519 REDACTED test\n", encoding="utf-8")
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            make_executable(bin_dir / "uci", "#!/bin/sh\nexit 0\n")
            section_result = subprocess.run(
                [HARDEN],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "BYPASS_AUTHORIZED_KEYS": str(key),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(section_result.returncode, 0)
            self.assertIn("no dropbear configuration", section_result.stderr)

    def test_filter_sync_requires_arguments_and_credentials(self) -> None:
        usage_result = subprocess.run(
            [FILTER_SYNC],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(usage_result.returncode, 0)
        self.assertIn("usage", usage_result.stderr)

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            helper = tmp / "plan"
            make_executable(helper, "#!/bin/sh\nexit 0\n")
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            make_executable(bin_dir / "uci", "#!/bin/sh\nexit 0\n")
            credential_result = subprocess.run(
                [FILTER_SYNC, "subscription", "group", "exclude"],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "DAED_PLAN_HELPER": str(helper),
                    "DAED_SYNC_LOCK": str(tmp / "sync.lock"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(credential_result.returncode, 0)
            self.assertIn("Missing daed dashboard credentials", credential_result.stderr)

    def test_filter_sync_rolls_back_group_changes_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            request_log = tmp / "requests.log"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *daed.config.listen_addr*) echo 127.0.0.1:2023 ;;\n"
                "  *daed.config.dashboard_username*) echo admin ;;\n"
                "  *daed.config.dashboard_password*) echo REDACTED_PASSWORD ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
            )
            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 0\n")
            make_executable(bin_dir / "ucode", "#!/bin/sh\nexec \"$@\"\n")
            make_executable(
                bin_dir / "plan",
                "#!/bin/sh\n"
                "printf '%s' '{\"subId\":\"sub1\",\"groupId\":\"group1\",\"subscriptionAttached\":true,\"subscriptionFilterRegex\":\"\",\"desiredCount\":1,\"excludedCount\":1,\"addCount\":1,\"staleCount\":1,\"addIds\":[\"new1\"],\"staleIds\":[\"old1\"]}'\n",
            )
            make_executable(
                bin_dir / "jsonfilter",
                "#!/bin/sh\n"
                "expression=''\n"
                "while [ \"$#\" -gt 0 ]; do case \"$1\" in -e) expression=$2; shift 2 ;; *) shift ;; esac; done\n"
                "case \"$expression\" in\n"
                "  @.data.token) echo token ;; @.subId) echo sub1 ;; @.groupId) echo group1 ;;\n"
                "  @.desiredCount) echo 1 ;; @.excludedCount) echo 1 ;; @.subscriptionAttached) echo true ;;\n"
                "  @.subscriptionFilterRegex) echo '' ;;\n"
                "  @.addIds) echo '[\"new1\"]' ;; @.staleIds) echo '[\"old1\"]' ;;\n"
                "  @.addCount) echo 1 ;; @.staleCount) echo 1 ;;\n"
                "esac\n",
            )
            make_executable(
                bin_dir / "curl",
                "#!/bin/sh\n"
                "body=''\n"
                "while [ \"$#\" -gt 0 ]; do case \"$1\" in --data-binary) body=${2#@}; shift 2 ;; -H|--max-time) shift 2 ;; -*) shift ;; *) shift ;; esac; done\n"
                "if grep -q 'query Token' \"$body\"; then printf '%s' '{\"data\":{\"token\":\"token\"}}'; exit 0; fi\n"
                "if grep -q 'query SyncState' \"$body\"; then printf '%s' '{\"data\":{\"subscriptions\":[],\"groups\":[]}}'; exit 0; fi\n"
                "if grep -q 'groupDelSubscriptions' \"$body\"; then echo detach >> \"$REQUEST_LOG\";\n"
                "elif grep -q 'groupAddSubscriptions' \"$body\"; then echo attach >> \"$REQUEST_LOG\";\n"
                "elif grep -q 'groupAddNodes' \"$body\"; then echo add >> \"$REQUEST_LOG\";\n"
                "elif grep -q 'groupDelNodes' \"$body\"; then echo remove >> \"$REQUEST_LOG\";\n"
                "elif grep -q 'run(dry:$dry)' \"$body\" && grep -q '\"dry\":true' \"$body\"; then echo validate >> \"$REQUEST_LOG\"; printf '%s' '{\"errors\":[{\"message\":\"invalid\"}]}'; exit 0;\n"
                "elif grep -q 'run(dry:$dry)' \"$body\"; then echo apply >> \"$REQUEST_LOG\"; fi\n"
                "printf '%s' '{\"data\":{\"ok\":true}}'\n",
            )
            result = subprocess.run(
                [FILTER_SYNC, "primary", "proxy", "备用"],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "REQUEST_LOG": str(request_log),
                    "DAED_PLAN_HELPER": str(bin_dir / "plan"),
                    "DAED_SYNC_LOCK": str(tmp / "filter.lock"),
                    "DAED_FILTER_LOG": str(tmp / "filter.log"),
                    "DAED_SKIP_UPDATE": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(request_log.exists(), result.stderr)
            self.assertEqual(
                request_log.read_text(encoding="utf-8").splitlines(),
                ["detach", "add", "remove", "validate", "add", "remove", "attach", "apply"],
            )

    def test_daed_configure_requires_luci_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            make_executable(bin_dir / "uci", "#!/bin/sh\nexit 1\n")
            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 0\n")
            result = subprocess.run(
                [DAED_CONFIGURE, "primary", "--confirm"],
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("configure daed credentials in LuCI first", result.stderr)

    def test_daed_configure_requires_running_daed_and_installed_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *dashboard_username*) echo admin ;;\n"
                "  *dashboard_password*) echo REDACTED_PASSWORD ;;\n"
                "esac\n",
            )
            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 1\n")
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
            stopped = subprocess.run(
                [DAED_CONFIGURE, "primary", "--confirm"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn("daed is not running", stopped.stderr)

            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 0\n")
            missing_helper = subprocess.run(
                [DAED_CONFIGURE, "primary", "--confirm"],
                env={
                    **env,
                    "DAED_OBJECT_ID_HELPER": str(tmp / "missing-helper"),
                    "DAED_SYNC_COMMAND": str(tmp / "missing-sync"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(missing_helper.returncode, 0)
            self.assertIn("object lookup helper is missing", missing_helper.stderr)

    def test_daed_configure_rejects_invalid_runtime_resolver(self) -> None:
        result = subprocess.run(
            [DAED_CONFIGURE, "primary", "--resolver", "not-an-ip", "--confirm"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolver is not a valid IPv4 address", result.stderr)

    def test_daed_configure_applies_public_ai_defaults_and_installs_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            uci_log = tmp / "uci.log"
            request_log = tmp / "graphql.log"
            sync_log = tmp / "sync.log"
            crontab = tmp / "root.cron"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *daed.config.dashboard_username*) echo admin ;;\n"
                "  *daed.config.dashboard_password*) echo REDACTED_PASSWORD ;;\n"
                "  *daed.config.listen_addr*) echo 0.0.0.0:2023 ;;\n"
                "  *) printf '%s\\n' \"$*\" >> \"$UCI_LOG\" ;;\n"
                "esac\n",
            )
            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 0\n")
            make_executable(
                bin_dir / "object-id",
                "#!/bin/sh\n[ \"$2\" = subscriptions ] && [ \"$3\" = primary ] && echo sub1\nexit 0\n",
            )
            make_executable(bin_dir / "sync", "#!/bin/sh\necho called >> \"$SYNC_LOG\"\n")
            make_executable(
                bin_dir / "curl",
                "#!/bin/sh\n"
                "body=''\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in --data-binary) body=${2#@}; shift 2 ;; -H|--max-time) shift 2 ;; -*) shift ;; *) shift ;; esac\n"
                "done\n"
                "sed 's/REDACTED_PASSWORD/REDACTED/g' \"$body\" >> \"$REQUEST_LOG\"\n"
                "if grep -q 'query Token' \"$body\"; then printf '%s' '{\"data\":{\"token\":\"token\"}}';\n"
                "elif grep -q 'query SetupState' \"$body\"; then printf '%s' '{\"data\":{\"configs\":[],\"routings\":[],\"groups\":[],\"subscriptions\":[{\"id\":\"sub1\",\"tag\":\"primary\"}]}}';\n"
                "elif grep -q 'createConfig' \"$body\"; then printf '%s' '{\"data\":{\"createConfig\":{\"id\":\"cfg1\"}}}';\n"
                "elif grep -q 'createRouting' \"$body\"; then printf '%s' '{\"data\":{\"createRouting\":{\"id\":\"route1\"}}}';\n"
                "elif grep -q 'createGroup' \"$body\"; then printf '%s' '{\"data\":{\"createGroup\":{\"id\":\"group1\"}}}';\n"
                "else printf '%s' '{\"data\":{\"ok\":true}}'; fi\n",
            )
            make_executable(
                bin_dir / "jsonfilter",
                "#!/bin/sh\n"
                "file=''\nexpression=''\n"
                "while [ \"$#\" -gt 0 ]; do case \"$1\" in -i) file=$2; shift 2 ;; -e) expression=$2; shift 2 ;; *) shift ;; esac; done\n"
                "case \"$expression\" in\n"
                "  @.data.token) echo token ;;\n"
                "  @.data.createConfig.id) echo cfg1 ;;\n"
                "  @.data.createRouting.id) echo route1 ;;\n"
                "  @.data.createGroup.id) echo group1 ;;\n"
                "esac\n",
            )
            result = subprocess.run(
                [DAED_CONFIGURE, "primary", "--resolver", "192.0.2.53", "--confirm"],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "UCI_LOG": str(uci_log),
                    "REQUEST_LOG": str(request_log),
                    "SYNC_LOG": str(sync_log),
                    "DAED_OBJECT_ID_HELPER": str(bin_dir / "object-id"),
                    "DAED_SYNC_COMMAND": str(bin_dir / "sync"),
                    "BYPASS_CRONTAB": str(crontab),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            requests = request_log.read_text(encoding="utf-8")
            self.assertIn("category-ai-!cn", requests)
            self.assertIn("min_moving_avg", requests)
            self.assertIn('"dry":true', requests)
            self.assertIn('"dry":false', requests)
            calls = uci_log.read_text(encoding="utf-8")
            self.assertIn("set bypass_router.daed.subscription_tag=primary", calls)
            self.assertIn("set bypass_router.daed.direct_resolver=192.0.2.53", calls)
            self.assertEqual(sync_log.read_text(encoding="utf-8").strip(), "called")
            self.assertEqual(crontab.read_text().count("daed-subscription-sync"), 1)

    def test_daed_sync_restores_remote_link_when_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            request_log = tmp / "requests.log"
            sync_log = tmp / "sync.log"
            make_executable(
                bin_dir / "uci",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *daed.config.listen_addr*) echo 127.0.0.1:2023 ;;\n"
                "  *daed.config.dashboard_username*) echo admin ;;\n"
                "  *daed.config.dashboard_password*) echo REDACTED_PASSWORD ;;\n"
                "  *bypass_router.daed.subscription_tag*) echo primary ;;\n"
                "  *bypass_router.daed.group*) echo proxy ;;\n"
                "  *bypass_router.daed.exclude_keyword*) echo 备用 ;;\n"
                "  *bypass_router.daed.direct_resolver*) exit 1 ;;\n"
                "  *network.lan.dns*) echo 192.0.2.53 ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
            )
            make_executable(bin_dir / "pidof", "#!/bin/sh\nexit 0\n")
            make_executable(
                bin_dir / "nslookup",
                "#!/bin/sh\nprintf '%s\\n' 'Name: source.example' 'Address: 198.51.100.10'\n",
            )
            make_executable(
                bin_dir / "curl",
                "#!/bin/sh\n"
                "body=''\noutput=''\nurl=''\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    --data-binary) body=${2#@}; shift 2 ;;\n"
                "    -o) output=$2; shift 2 ;;\n"
                "    -H|--connect-timeout|--max-time|--retry|--retry-delay|--resolve) shift 2 ;;\n"
                "    -*) shift ;;\n"
                "    *) url=$1; shift ;;\n"
                "  esac\n"
                "done\n"
                "if [ -z \"$body\" ]; then printf '%s' 'subscription-data' > \"$output\"; exit 0; fi\n"
                "if grep -q 'query Token' \"$body\"; then printf '%s' '{\"data\":{\"token\":\"token\"}}'; exit 0; fi\n"
                "if grep -q 'query SyncSource' \"$body\"; then printf '%s' '{\"data\":{\"subscriptions\":[]}}'; exit 0; fi\n"
                "if grep -q 'updateSubscriptionLink' \"$body\"; then\n"
                "  if grep -q '127.0.0.1/cgi-bin/daede-sub' \"$body\"; then echo local-link >> \"$REQUEST_LOG\"; else echo restore-link >> \"$REQUEST_LOG\"; fi\n"
                "  printf '%s' '{\"data\":{\"updateSubscriptionLink\":{\"id\":\"sub1\"}}}'; exit 0; fi\n"
                "if grep -q 'updateSubscription(id' \"$body\"; then echo update-subscription >> \"$REQUEST_LOG\"; [ \"${FAIL_SUBSCRIPTION_UPDATE:-0}\" = 1 ] && exit 22; printf '%s' '{\"data\":{\"updateSubscription\":{\"id\":\"sub1\"}}}'; exit 0; fi\n"
                "if grep -q 'updateSubscriptionCron' \"$body\"; then echo disable-cron >> \"$REQUEST_LOG\"; printf '%s' '{\"data\":{\"updateSubscriptionCron\":{\"id\":\"sub1\"}}}'; exit 0; fi\n"
                "echo unmatched >> \"$REQUEST_LOG\"\nsed 's#/subscription#/REDACTED#g' \"$body\" >> \"$REQUEST_LOG\"\nexit 1\n",
            )
            make_executable(
                bin_dir / "jsonfilter",
                "#!/bin/sh\n"
                "file=''\nexpression=''\n"
                "while [ \"$#\" -gt 0 ]; do case \"$1\" in -i) file=$2; shift 2 ;; -e) expression=$2; shift 2 ;; *) shift ;; esac; done\n"
                "case \"$expression\" in @.data.token) echo token ;; @.id) echo sub1 ;; @.link) echo https://source.example/subscription ;; esac\n",
            )
            make_executable(
                bin_dir / "meta-helper",
                "#!/bin/sh\nprintf '%s' '{\"id\":\"sub1\",\"link\":\"https://source.example/subscription\"}'\n",
            )
            make_executable(
                bin_dir / "filter-sync",
                "#!/bin/sh\nprintf '%s\\n' 'Filtered subscription synced: selected=7 excluded=2'\n",
            )
            sync_env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "REQUEST_LOG": str(request_log),
                "DAED_SYNC_LOG": str(sync_log),
                "DAED_META_HELPER": str(bin_dir / "meta-helper"),
                "DAED_FILTER_SYNC": str(bin_dir / "filter-sync"),
                "DAED_SYNC_LOCK": str(tmp / "sync.lock"),
            }
            result = subprocess.run(
                [DAED_SYNC],
                env={**sync_env, "FAIL_SUBSCRIPTION_UPDATE": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                request_log.exists(),
                f"sync stopped before GraphQL update:\nstdout={result.stdout}\nstderr={result.stderr}\n"
                f"log={sync_log.read_text(encoding='utf-8') if sync_log.exists() else '<missing>'}",
            )
            requests = request_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(requests[:3], ["local-link", "update-subscription", "restore-link"])
            visible = result.stdout + result.stderr + sync_log.read_text(encoding="utf-8")
            self.assertNotIn("source.example", visible)

            request_log.write_text("", encoding="utf-8")
            success = subprocess.run(
                [DAED_SYNC],
                env=sync_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(
                request_log.read_text(encoding="utf-8").splitlines(),
                ["local-link", "update-subscription", "restore-link", "disable-cron"],
            )

    def test_public_router_helpers_are_executable(self) -> None:
        helpers = (
            DAED_CONFIGURE,
            DAED_SYNC,
            GEO_UPDATE,
            ROOT / "files" / "usr" / "share" / "luci-app-daede" / "daed-object-id.uc",
            ROOT / "files" / "usr" / "share" / "luci-app-daede" / "daed-subscription-meta.uc",
        )
        for helper in helpers:
            with self.subTest(helper=helper):
                self.assertTrue(os.access(helper, os.X_OK), str(helper))

    def test_repository_does_not_bake_in_private_addresses_or_mac_addresses(self) -> None:
        forbidden_patterns = (
            re.compile(r"(?<![0-9])10(?:\.[0-9]{1,3}){3}(?![0-9])"),
            re.compile(r"(?<![0-9])192\.168(?:\.[0-9]{1,3}){2}(?![0-9])"),
            re.compile(r"(?<![0-9])172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2}(?![0-9])"),
            re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])"),
        )
        files = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for relative in files:
            path = ROOT / relative
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in forbidden_patterns:
                self.assertIsNone(pattern.search(content), str(path))


if __name__ == "__main__":
    unittest.main()
