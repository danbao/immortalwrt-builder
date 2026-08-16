import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryBaselineTests(unittest.TestCase):
    def test_firmware_build_has_no_package_or_file_overrides(self) -> None:
        workflow = (ROOT / ".github/workflows/build-openwrt.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('IB_TARGET: x86/generic', workflow)
        self.assertIn('make manifest PROFILE="generic"', workflow)
        self.assertIn('make image PROFILE="generic"', workflow)
        self.assertIn("--architecture i386_pentium4", workflow)
        self.assertNotIn("PACKAGES=", workflow)
        self.assertNotIn("FILES=", workflow)
        self.assertNotIn("download-release-asset", workflow)
        self.assertIn("forbidden_packages=", workflow)
        for package in (
            "luci-app-passwall2",
            "luci-app-openclash",
            "luci-app-nikki",
            "mihomo-meta",
            "v2ray-geoip",
            "v2ray-geosite",
        ):
            self.assertIn(package, workflow)
        self.assertFalse((ROOT / "config/openwrt-packages.txt").exists())
        removed_profile = ROOT / "config" / ("openwrt-packages-" + "da" + "ed.txt")
        self.assertFalse(removed_profile.exists())
        files = ROOT / "files"
        self.assertFalse(files.exists() and any(path.is_file() for path in files.rglob("*")))

    def test_removed_proxy_and_dns_customizations_do_not_reappear(self) -> None:
        forbidden = ("da" + "ed", "dae" + "de", "mos" + "dns")
        roots = (ROOT / "config", ROOT / "scripts")
        matches: list[str] = []
        for base in roots:
            for path in base.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if any(term in text or term in path.name.lower() for term in forbidden):
                    matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_package_metadata_registry_contains_only_upstream_overrides(self) -> None:
        payload = json.loads(
            (ROOT / "config/third-party-components.json").read_text(encoding="utf-8")
        )
        sources = {component["source"] for component in payload["components"]}
        self.assertEqual(
            sources,
            {
                "https://github.com/immortalwrt/immortalwrt",
                "https://github.com/immortalwrt/luci",
            },
        )


if __name__ == "__main__":
    unittest.main()
