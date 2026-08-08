import os
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
            self.assertIn("network reload", init_log.read_text(encoding="utf-8"))

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
                "  *'neigh show'*) echo '192.0.2.2 dev br-lan lladdr 00:11:22:33:44:55 REACHABLE' ;;\n"
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

    def test_repository_does_not_bake_in_site_addresses(self) -> None:
        forbidden_fragment = ".".join(("10", "10", "0"))
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
            self.assertNotIn(forbidden_fragment, content, str(path))


if __name__ == "__main__":
    unittest.main()
