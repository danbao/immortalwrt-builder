import unittest
from pathlib import Path
from xml.etree import ElementTree

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_img_to_ova


class OpenWrtImgToOvaTests(unittest.TestCase):
    def test_scan_defaults_to_one_network_adapter(self) -> None:
        args = openwrt_img_to_ova.parse_args(["scan"])
        self.assertEqual(args.nic_count, 1)

    def test_ovf_can_define_two_distinct_vmware_network_adapters(self) -> None:
        ovf = openwrt_img_to_ova.make_ovf("router", "router.vmdk", 10, 20, 2)
        root = ElementTree.fromstring(ovf)
        namespace = {"ovf": "http://schemas.dmtf.org/ovf/envelope/1"}
        networks = root.findall(".//ovf:Network", namespace)
        network_names = [
            item.attrib["{http://schemas.dmtf.org/ovf/envelope/1}name"]
            for item in networks
        ]
        self.assertEqual(network_names, ["LAN1", "LAN2"])
        self.assertIn("<rasd:Connection>LAN1</rasd:Connection>", ovf)
        self.assertIn("<rasd:Connection>LAN2</rasd:Connection>", ovf)

    def test_build_workflow_requests_one_network_adapter(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-openwrt.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("--nic-count 1", workflow)

    def test_build_workflow_pins_compatible_immortalwrt_release(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-openwrt.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('IB_VERSION: "25.12.1"', workflow)
        self.assertNotIn("ib_version:", workflow)
        self.assertIn("IB_TARGET: x86/generic", workflow)
        self.assertIn("IB_PACKAGE_ARCH: i386_pentium4", workflow)

    def test_daed_release_metadata_uses_distinct_tag_title_and_assets(self) -> None:
        tag, title, artifact = openwrt_img_to_ova.release_metadata(
            "immortalwrt-x86-64-daed",
            "123abc456def",
            release_date="20260713",
            immortalwrt_commit="cf234f8de6d5",
        )
        self.assertEqual(tag, "openwrt-immortalwrt-x86-64-daed-20260713-cf234f8de6d5-123abc456def")
        self.assertEqual(title, "ImmortalWrt x86_64 daed ESXi OVA - 20260713 cf234f8de6d5")
        self.assertEqual(artifact, "immortalwrt-x86-64-daed-20260713-cf234f8de6d5-123abc456def")

    def test_standard_release_metadata_is_unchanged(self) -> None:
        tag, title, artifact = openwrt_img_to_ova.release_metadata(
            "immortalwrt-x86-64",
            "123abc456def",
            release_date="20260713",
            immortalwrt_commit="cf234f8de6d5",
        )
        self.assertEqual(tag, "openwrt-immortalwrt-x86-64-20260713-cf234f8de6d5-123abc456def")
        self.assertEqual(title, "ImmortalWrt x86_64 ESXi OVA - 20260713 cf234f8de6d5")
        self.assertEqual(artifact, "immortalwrt-x86-64-20260713-cf234f8de6d5-123abc456def")


if __name__ == "__main__":
    unittest.main()
