import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_releases


class PublishReleasesTests(unittest.TestCase):
    def test_existing_release_full_sha_rejects_short_hash_collision(self) -> None:
        tag = "openwrt-immortalwrt-x86-generic-20260726-cf123-aaaaaaaaaaaa"
        metadata_payload = {
            "results": {
                "built": [
                    {
                        "release_tag": tag,
                        "image_sha256": "a" * 12 + "b" * 52,
                    }
                ]
            }
        }

        def fake_run(command: list[str], *, check: bool = True):
            target = Path(command[command.index("--dir") + 1]) / "build-metadata.json"
            target.write_text(json.dumps(metadata_payload), encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(publish_releases, "release_asset_names", return_value={"build-metadata.json"}),
            mock.patch.object(publish_releases, "run", side_effect=fake_run),
        ):
            self.assertEqual(
                publish_releases.existing_release_image_sha256(tag),
                "a" * 12 + "b" * 52,
            )

    def test_managed_release_family_recognizes_only_base_tags(self) -> None:
        self.assertEqual(
            publish_releases.managed_release_family("openwrt-immortalwrt-x86-generic-20260713-cf234f8de6d5-123abc456def"),
            "base",
        )
        self.assertIsNone(
            publish_releases.managed_release_family(
                "openwrt-immortalwrt-x86-64-20260713-cf234f8de6d5-123abc456def"
            )
        )
        self.assertIsNone(publish_releases.managed_release_family("manual-release"))

    def test_managed_release_image_key_uses_family_and_image_sha(self) -> None:
        self.assertEqual(
            publish_releases.managed_release_image_key(
                "openwrt-immortalwrt-x86-generic-20260713-cf234f8de6d5-123abc456def"
            ),
            ("base", "123abc456def"),
        )
        self.assertIsNone(publish_releases.managed_release_image_key("manual-release"))

    def test_expected_asset_paths_includes_release_metadata(self) -> None:
        item = {
            "release_tag": "openwrt-immortalwrt-x86-generic-20260710-cf234f8de6d5-aaaaaaaaaaaa",
            "ova_path": "dist/demo.ova",
            "checksum_path": "dist/demo.ova.sha256",
            "image_path": "build-out/immortalwrt-x86-generic.img.gz",
            "image_asset": "immortalwrt-x86-generic-20260710.img.gz",
        }
        paths = publish_releases.expected_asset_paths(item, Path("dist/metadata"))
        self.assertEqual(
            paths,
            [
                Path("dist/demo.ova"),
                Path("dist/demo.ova.sha256"),
                Path("dist/immortalwrt-x86-generic-20260710.img.gz"),
                Path("dist/immortalwrt-x86-generic-20260710.img.gz.sha256"),
                Path("dist/metadata/base/build-metadata.json"),
                Path("dist/metadata/base/packages.spdx.json"),
                Path("dist/metadata/base/upstream-provenance.json"),
                Path("dist/metadata/base/source-inventory.json"),
            ],
        )

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

    def test_validate_publish_item_rejects_unmanaged_tag_and_path_escape(self) -> None:
        item = {
            "release_tag": "malicious",
            "image_sha256": "a" * 64,
            "ova_path": "dist/demo.ova",
            "checksum_path": "dist/demo.ova.sha256",
            "image_path": "build-out/demo.img.gz",
            "image_asset": "immortalwrt-x86-generic-demo.img.gz",
        }
        with self.assertRaisesRegex(ValueError, "unmanaged release tag"):
            publish_releases.validate_publish_item(item, Path("dist/metadata"))

        item["release_tag"] = "openwrt-immortalwrt-x86-generic-20260713-cf234f8de6d5-aaaaaaaaaaaa"
        item["ova_path"] = "../outside.ova"
        with self.assertRaisesRegex(ValueError, "outside dist"):
            publish_releases.validate_publish_item(item, Path("dist/metadata"))

    def test_prune_old_releases_keeps_latest_base_release(self) -> None:
        releases = [
            {"tagName": "openwrt-immortalwrt-x86-generic-20260713-cf-aaa111aaa111", "createdAt": "2026-07-13T00:00:00Z"},
            {"tagName": "openwrt-immortalwrt-x86-generic-20260712-cf-bbb222bbb222", "createdAt": "2026-07-12T00:00:00Z"},
            {"tagName": "manual-release", "createdAt": "2026-07-13T00:00:00Z"},
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
                "openwrt-immortalwrt-x86-generic-20260712-cf-bbb222bbb222",
            ],
        )


if __name__ == "__main__":
    unittest.main()
