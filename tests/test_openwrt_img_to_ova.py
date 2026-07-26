import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_img_to_ova


class OpenWrtImgToOvaTests(unittest.TestCase):
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
