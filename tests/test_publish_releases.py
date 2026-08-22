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

    def test_release_asset_paths_require_explicit_nonempty_list(self) -> None:
        item = {"release_assets": ["dist/demo.ova", "dist/SHA256SUMS"]}
        self.assertEqual(
            publish_releases.release_asset_paths(item),
            [Path("dist/demo.ova"), Path("dist/SHA256SUMS")],
        )
        with self.assertRaisesRegex(ValueError, "release_assets"):
            publish_releases.release_asset_paths({})

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

    def test_publish_existing_release_uploads_declared_assets_and_preserves_unmanaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            assets = [tmp / name for name in ("demo.ova", "SHA256SUMS", "build-metadata.json")]
            for asset in assets:
                asset.write_text("data\n", encoding="utf-8")
            item = {
                "release_tag": "openwrt-immortalwrt-x86-64-daed-20260713-cf-123abc456def",
                "release_title": "Demo",
                "release_assets": [str(path) for path in assets],
                "image_sha256": "abc",
                "builder_version": "10",
                "build_metadata": {
                    "source": "official-immortalwrt",
                    "profile": {"imagebuilder_profile": "generic", "rootfs_partsize": 2048, "nic_count": 1},
                    "packages": {"daed": "1.27.0-r1"},
                    "provenance": {"workflow_run_url": "https://github.com/example/actions/runs/1"},
                },
            }
            calls: list[list[str]] = []

            def fake_run(command: list[str], *, check: bool = True):
                calls.append(command)
                return mock.Mock(returncode=0, stdout="{}", stderr="")

            remote_assets = [
                {"name": "demo.ova"},
                {"name": "SHA256SUMS"},
                {"name": "build-metadata.json"},
                {"name": "immortalwrt-x86-64-daed-old.ova.sha256"},
                {"name": "manual-notes.txt"},
            ]
            with mock.patch.object(publish_releases, "release_exists", return_value=True), mock.patch.object(
                publish_releases, "list_release_assets", return_value=remote_assets
            ), mock.patch.object(publish_releases, "run", side_effect=fake_run):
                self.assertFalse(publish_releases.publish_item(item))

            upload = next(command for command in calls if command[:3] == ["gh", "release", "upload"])
            self.assertEqual(upload[4:-1], [str(path) for path in assets])
            deleted = [command[4] for command in calls if command[:3] == ["gh", "release", "delete-asset"]]
            self.assertEqual(deleted, ["immortalwrt-x86-64-daed-old.ova.sha256"])
            edit = next(command for command in calls if command[:3] == ["gh", "release", "edit"])
            notes = edit[edit.index("--notes") + 1]
            self.assertIn("official-immortalwrt", notes)
            self.assertIn("daed: `1.27.0-r1`", notes)


if __name__ == "__main__":
    unittest.main()
