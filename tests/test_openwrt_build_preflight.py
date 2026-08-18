import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_build_preflight as preflight


class OpenWrtBuildPreflightTests(unittest.TestCase):
    def test_daed_feed_config_excludes_conflicting_proxy_stacks(self) -> None:
        config_file = Path(__file__).resolve().parents[1] / "config" / "daed-feed.json"
        config = preflight.load_daed_config(config_file)
        self.assertEqual(config["sdk"], "25.12")
        self.assertEqual(config["arch"], "x86_64")
        packages = set(config["packages"])
        self.assertIn("daed", packages)
        self.assertIn("luci-app-daede", packages)
        self.assertNotIn("luci-app-passwall2", packages)
        self.assertNotIn("luci-app-openclash", packages)
        self.assertNotIn("luci-app-nikki", packages)
        self.assertNotIn("luci-app-mosdns", packages)

    def test_load_daed_config_rejects_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            config_file = Path(tmp_s) / "daed-feed.json"
            config_file.write_text(json.dumps({"base_url": "https://feed.test/daed"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required key"):
                preflight.load_daed_config(config_file)

            config_file.write_text(
                json.dumps(
                    {
                        "base_url": "https://feed.test/daed",
                        "sdk": "25.12",
                        "arch": "x86_64",
                        "packages": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-empty list"):
                preflight.load_daed_config(config_file)

    def test_parse_daed_manifest(self) -> None:
        payload = (
            "daed=daed-2026.08.13-r2.apk\n"
            "daed_sha256=5bd132fcf498d55d990a7f28af572c94facbef1b0299b995a38992e92de15e06\n"
            "luci-app-daede=luci-app-daede-1.14.7-r23.apk\n"
            "luci-app-daede_sha256=b25b8f3107cb618729996b0665950406839d88d02f14ff38b2fb9d50b1244639\n"
        )
        manifest = preflight.parse_daed_manifest(payload)
        self.assertEqual(manifest["daed"]["filename"], "daed-2026.08.13-r2.apk")
        self.assertEqual(
            manifest["daed"]["sha256"],
            "5bd132fcf498d55d990a7f28af572c94facbef1b0299b995a38992e92de15e06",
        )
        self.assertEqual(manifest["luci-app-daede"]["filename"], "luci-app-daede-1.14.7-r23.apk")

        with self.assertRaisesRegex(ValueError, "missing a filename"):
            preflight.parse_daed_manifest("daed_sha256=" + "a" * 64 + "\n")
        with self.assertRaisesRegex(ValueError, "invalid daed manifest line"):
            preflight.parse_daed_manifest("garbage line without equals\n")

    def test_extract_package_version(self) -> None:
        self.assertEqual(preflight.extract_package_version("daed-2026.08.13-r2.apk", "daed"), "2026.08.13-r2")
        self.assertEqual(
            preflight.extract_package_version("luci-app-daede-1.14.7-r23.apk", "luci-app-daede"),
            "1.14.7-r23",
        )
        with self.assertRaisesRegex(ValueError, "does not match package"):
            preflight.extract_package_version("other-1.0.apk", "daed")

    def test_cmd_daed_packages_downloads_and_records(self) -> None:
        daed_body = b"daed-apk-body"
        luci_body = b"luci-apk-body"
        manifest_payload = (
            f"daed=daed-2026.08.13-r2.apk\n"
            f"daed_sha256={hashlib.sha256(daed_body).hexdigest()}\n"
            "luci-app-daede=luci-app-daede-1.14.7-r23.apk\n"
            f"luci-app-daede_sha256={hashlib.sha256(luci_body).hexdigest()}\n"
        )

        def fake_fetch(url: str, **kwargs: object) -> bytes:
            if url.endswith("manifest-daede.txt"):
                return manifest_payload.encode("utf-8")
            raise AssertionError(f"unexpected fetch: {url}")

        def fake_download(url: str, output: Path, **kwargs: object) -> None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(daed_body if "/daed-" in url else luci_body)

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            config_file = tmp / "daed-feed.json"
            config_file.write_text(
                json.dumps(
                    {
                        "base_url": "https://feed.test/daed",
                        "sdk": "25.12",
                        "arch": "x86_64",
                        "packages": ["daed", "luci-app-daede"],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=config_file,
                out_dir=tmp / "packages",
                metadata_out=tmp / "daed-packages.json",
                timeout=1,
                retries=1,
            )
            with mock.patch.object(preflight, "fetch_bytes", side_effect=fake_fetch), mock.patch.object(
                preflight, "download_url", side_effect=fake_download
            ):
                self.assertEqual(preflight.cmd_daed_packages(args), 0)

            meta = json.loads((tmp / "daed-packages.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["packages"]["daed"]["version"], "2026.08.13-r2")
            self.assertEqual(meta["packages"]["daed"]["sha256"], hashlib.sha256(daed_body).hexdigest())
            self.assertEqual(meta["packages"]["luci-app-daede"]["version"], "1.14.7-r23")
            self.assertEqual((tmp / "packages" / "daed-2026.08.13-r2.apk").read_bytes(), daed_body)

    def test_cmd_daed_packages_rejects_sha_mismatch(self) -> None:
        manifest_payload = (
            "daed=daed-2026.08.13-r2.apk\n"
            "daed_sha256=" + "a" * 64 + "\n"
        )

        def fake_fetch(url: str, **kwargs: object) -> bytes:
            return manifest_payload.encode("utf-8")

        def fake_download(url: str, output: Path, **kwargs: object) -> None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"tampered body")

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            config_file = tmp / "daed-feed.json"
            config_file.write_text(
                json.dumps(
                    {
                        "base_url": "https://feed.test/daed",
                        "sdk": "25.12",
                        "arch": "x86_64",
                        "packages": ["daed"],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=config_file,
                out_dir=tmp / "packages",
                metadata_out=tmp / "daed-packages.json",
                timeout=1,
                retries=1,
            )
            with mock.patch.object(preflight, "fetch_bytes", side_effect=fake_fetch), mock.patch.object(
                preflight, "download_url", side_effect=fake_download
            ):
                with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                    preflight.cmd_daed_packages(args)

    def test_cmd_daed_packages_requires_manifest_entry(self) -> None:
        def fake_fetch(url: str, **kwargs: object) -> bytes:
            return b"other=other-1.0.apk\nother_sha256=" + b"a" * 64 + b"\n"

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            config_file = tmp / "daed-feed.json"
            config_file.write_text(
                json.dumps(
                    {
                        "base_url": "https://feed.test/daed",
                        "sdk": "25.12",
                        "arch": "x86_64",
                        "packages": ["daed"],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=config_file,
                out_dir=tmp / "packages",
                metadata_out=tmp / "daed-packages.json",
                timeout=1,
                retries=1,
            )
            with mock.patch.object(preflight, "fetch_bytes", side_effect=fake_fetch):
                with self.assertRaisesRegex(ValueError, "no entry for required package"):
                    preflight.cmd_daed_packages(args)

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

    def test_copy_raw_images_handles_multiple_built_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            build_out = tmp / "build-out"
            dist = tmp / "dist"
            build_out.mkdir()
            standard = build_out / "immortalwrt-x86-64.img.gz"
            daed = build_out / "immortalwrt-x86-64-daed.img.gz"
            standard.write_bytes(b"standard")
            daed.write_bytes(b"daed")
            results = tmp / "build-results.json"
            results.write_text(
                json.dumps(
                    {
                        "built": [
                            {
                                "image_path": str(standard),
                                "image_asset": "immortalwrt-x86-64-20260713.img.gz",
                            },
                            {
                                "image_path": str(daed),
                                "image_asset": "immortalwrt-x86-64-daed-20260713.img.gz",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(results=results, source_dir=build_out, out_dir=dist)
            self.assertEqual(preflight.cmd_copy_raw_images(args), 0)
            self.assertEqual((dist / "immortalwrt-x86-64-20260713.img.gz").read_bytes(), b"standard")
            self.assertEqual((dist / "immortalwrt-x86-64-daed-20260713.img.gz").read_bytes(), b"daed")


if __name__ == "__main__":
    unittest.main()
