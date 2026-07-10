import argparse
import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_build_preflight as preflight


class OpenWrtBuildPreflightTests(unittest.TestCase):
    def test_read_packages_ignores_comments_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            package_file = Path(tmp_s) / "packages.txt"
            package_file.write_text(
                """
                # base UI
                luci luci-base
                curl # inline comment
                """,
                encoding="utf-8",
            )
            self.assertEqual(preflight.read_packages(package_file), ["luci", "luci-base", "curl"])

            package_file.write_text("luci\nluci\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate package"):
                preflight.read_packages(package_file)

    def test_read_feeds_parses_required_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            feed_file = Path(tmp_s) / "feeds.tsv"
            feed_file.write_text(
                "# name\turl\trequired\nnikki\thttps://example.test/feed/\ttrue\noptional\thttps://example.test/optional\tfalse\n",
                encoding="utf-8",
            )
            feeds = preflight.read_feeds(feed_file)
            self.assertEqual(feeds[0], preflight.Feed("nikki", "https://example.test/feed", True))
            self.assertEqual(feeds[1], preflight.Feed("optional", "https://example.test/optional", False))

    def test_select_release_asset_requires_exactly_one_match(self) -> None:
        assets = [{"name": "luci-app-demo_1_all.ipk"}, {"name": "demo.tar.gz"}]
        self.assertEqual(preflight.select_release_asset(assets, "luci-app-demo_*_all.ipk")["name"], "luci-app-demo_1_all.ipk")
        with self.assertRaisesRegex(ValueError, "found 0"):
            preflight.select_release_asset(assets, "missing*")
        with self.assertRaisesRegex(ValueError, "found 2"):
            preflight.select_release_asset(assets, "*")

    def test_parse_sha256sums_requires_one_archive_entry(self) -> None:
        payload = "abc123  immortalwrt-imagebuilder-24.10.6-x86-64.Linux-x86_64.tar.zst\n"
        self.assertEqual(
            preflight.parse_sha256sums(payload, "immortalwrt-imagebuilder-24.10.6-x86-64.Linux-x86_64.tar.zst"),
            "abc123",
        )
        with self.assertRaisesRegex(ValueError, "found 0"):
            preflight.parse_sha256sums(payload, "missing.tar.zst")

    def test_verify_records_checks_manifest_and_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            manifest = tmp / "converted-images.json"
            doc = tmp / "converted-images.md"
            manifest.write_text(
                json.dumps({"conversions": {"abc:7": {"release_tag": "openwrt-demo"}}}),
                encoding="utf-8",
            )
            doc.write_text("release `openwrt-demo`\n", encoding="utf-8")
            args = argparse.Namespace(
                manifest=manifest,
                doc=doc,
                release_tag=["openwrt-demo"],
                check_latest_release=False,
                repo=None,
                timeout=1,
                retries=1,
            )
            self.assertEqual(preflight.cmd_verify_records(args), 0)

            args.release_tag = ["missing"]
            with self.assertRaisesRegex(RuntimeError, "missing from manifest"):
                preflight.cmd_verify_records(args)


if __name__ == "__main__":
    unittest.main()
