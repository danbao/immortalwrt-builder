import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_releases


class PublishReleasesTests(unittest.TestCase):
    def test_managed_release_family_recognizes_standard_and_daed_tags(self) -> None:
        self.assertEqual(
            publish_releases.managed_release_family("openwrt-immortalwrt-x86-64-20260713-cf234f8de6d5-123abc456def"),
            "standard",
        )
        self.assertEqual(
            publish_releases.managed_release_family("openwrt-immortalwrt-x86-64-daed-20260713-cf234f8de6d5-123abc456def"),
            "daed",
        )
        self.assertIsNone(publish_releases.managed_release_family("manual-release"))

    def test_expected_asset_paths_uses_dist_image_asset_by_default(self) -> None:
        item = {
            "ova_path": "dist/demo.ova",
            "checksum_path": "dist/demo.ova.sha256",
            "image_path": "build-out/immortalwrt-x86-64.img.gz",
            "image_asset": "immortalwrt-x86-64-20260710.img.gz",
        }
        ova, checksum, image = publish_releases.expected_asset_paths(item)
        self.assertEqual(ova, Path("dist/demo.ova"))
        self.assertEqual(checksum, Path("dist/demo.ova.sha256"))
        self.assertEqual(image, Path("dist/immortalwrt-x86-64-20260710.img.gz"))

    def test_require_file_rejects_missing_and_empty_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            missing = tmp / "missing.ova"
            with self.assertRaises(FileNotFoundError):
                publish_releases.require_file(missing)

            empty = tmp / "empty.ova"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "empty"):
                publish_releases.require_file(empty)

            nonempty = tmp / "demo.ova"
            nonempty.write_bytes(b"data")
            publish_releases.require_file(nonempty)

    def test_prune_old_releases_keeps_each_family_separately(self) -> None:
        releases = [
            {"tagName": "openwrt-immortalwrt-x86-64-20260713-cf-aaa111aaa111", "createdAt": "2026-07-13T00:00:00Z"},
            {"tagName": "openwrt-immortalwrt-x86-64-20260712-cf-bbb222bbb222", "createdAt": "2026-07-12T00:00:00Z"},
            {"tagName": "openwrt-immortalwrt-x86-64-daed-20260713-cf-ccc333ccc333", "createdAt": "2026-07-13T00:00:00Z"},
            {"tagName": "openwrt-immortalwrt-x86-64-daed-20260712-cf-ddd444ddd444", "createdAt": "2026-07-12T00:00:00Z"},
        ]
        deleted: list[str] = []

        def fake_run(command: list[str], *, check: bool = True):
            deleted.append(command[3])
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(publish_releases, "list_releases", return_value=releases), mock.patch.object(publish_releases, "run", side_effect=fake_run):
            publish_releases.prune_old_releases(1)

        self.assertEqual(
            deleted,
            [
                "openwrt-immortalwrt-x86-64-daed-20260712-cf-ddd444ddd444",
                "openwrt-immortalwrt-x86-64-20260712-cf-bbb222bbb222",
            ],
        )


if __name__ == "__main__":
    unittest.main()
