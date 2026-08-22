import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_releases


class PublishReleasesTests(unittest.TestCase):
    def make_release_item(self, root: Path) -> dict[str, object]:
        image_body = b"raw-image"
        image_sha = hashlib.sha256(image_body).hexdigest()
        suffix = image_sha[:12]
        image_name = f"immortalwrt-x86-64-daed-20260713-cf-{suffix}.img.gz"
        names = [
            image_name,
            f"immortalwrt-x86-64-daed-esxi-20260713-cf-{suffix}.ova",
            f"immortalwrt-x86-64-daed-esxi-20260713-cf-{suffix}.ova.sha256",
            f"immortalwrt-x86-64-daed-20260713-cf-{suffix}.manifest",
            f"immortalwrt-x86-64-daed-20260713-cf-{suffix}.bom.cdx.json",
            "build-metadata.json",
            "build-metadata.tar.gz",
        ]
        for name in names:
            (root / name).write_bytes(f"payload:{name}".encode())
        (root / image_name).write_bytes(image_body)
        metadata = {
            "source": "official-immortalwrt",
            "profile": {"imagebuilder_profile": "generic", "rootfs_partsize": 2048, "nic_count": 1},
            "packages": {
                "daed": "1.27.0-r1",
                "daed-geoip": "20260101-r1",
                "daed-geosite": "20260101-r1",
                "luci-app-daed": "1.1.0-r2",
                "luci-i18n-daed-zh-cn": "1.1.0-r2",
            },
            "provenance": {
                "repository_commit": "deadbeef",
                "workflow_run_url": "https://github.com/example/actions/runs/1",
            },
        }
        (root / "build-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        ova = root / names[1]
        (root / names[2]).write_text(f"{hashlib.sha256(ova.read_bytes()).hexdigest()}  {ova.name}\n", encoding="utf-8")
        sums = root / "SHA256SUMS"
        sums.write_text(
            "".join(
                f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n"
                for name in sorted(names)
            ),
            encoding="utf-8",
        )
        return {
            "release_tag": f"openwrt-{image_name.removesuffix('.img.gz')}",
            "release_title": "Demo",
            "release_assets": [str(root / name) for name in [*names, sums.name]],
            "image_asset": image_name,
            "image_sha256": image_sha,
            "ova_path": str(ova),
            "checksum_path": str(root / names[2]),
            "builder_version": "10",
            "build_metadata": metadata,
        }

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
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            item = self.make_release_item(root)
            self.assertEqual(len(publish_releases.release_asset_paths(item, root)), 8)
            with self.assertRaisesRegex(ValueError, "release_assets"):
                publish_releases.release_asset_paths({}, root)
            item["release_assets"] = [str(root / "../escape.ova")]
            with self.assertRaisesRegex(ValueError, "outside trusted asset directory"):
                publish_releases.release_asset_paths(item, root)

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
            item = self.make_release_item(tmp)
            assets = [Path(path) for path in item["release_assets"]]
            calls: list[list[str]] = []

            def fake_run(command: list[str], *, check: bool = True):
                calls.append(command)
                return mock.Mock(returncode=0, stdout="{}", stderr="")

            remote_assets = [
                *(
                    {"name": path.name, "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"}
                    for path in assets
                ),
                {"name": "immortalwrt-x86-64-daed-old.ova.sha256"},
                {"name": "manual-notes.txt"},
            ]
            with mock.patch.object(publish_releases, "release_exists", return_value=True), mock.patch.object(
                publish_releases, "list_release_assets", return_value=remote_assets
            ), mock.patch.object(publish_releases, "run", side_effect=fake_run):
                self.assertFalse(publish_releases.publish_item(item, tmp))

            self.assertFalse(any(command[:3] == ["gh", "release", "upload"] for command in calls))
            self.assertFalse(any(command[:3] == ["gh", "release", "edit"] for command in calls))
            deleted = [command[4] for command in calls if command[:3] == ["gh", "release", "delete-asset"]]
            self.assertEqual(deleted, ["immortalwrt-x86-64-daed-old.ova.sha256"])

    def test_existing_release_rejects_remote_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            item = self.make_release_item(root)
            remote_assets = [
                {"name": Path(path).name, "digest": "sha256:" + "0" * 64}
                for path in item["release_assets"]
            ]
            with mock.patch.object(publish_releases, "release_exists", return_value=True), mock.patch.object(
                publish_releases, "list_release_assets", return_value=remote_assets
            ):
                with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                    publish_releases.publish_item(item, root)

    def test_release_payload_rejects_checksum_mismatch_and_unrelated_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            item = self.make_release_item(root)
            publish_releases.validate_release_payload(
                item,
                root,
                expected_repository_commit="deadbeef",
                expected_workflow_run_url="https://github.com/example/actions/runs/1",
            )
            (root / "build-metadata.tar.gz").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                publish_releases.validate_release_payload(item, root)

            item = self.make_release_item(root)
            item["release_tag"] = "manual-release"
            with self.assertRaisesRegex(ValueError, "managed release tag"):
                publish_releases.validate_release_payload(item, root)

            item = self.make_release_item(root)
            with self.assertRaisesRegex(ValueError, "repository_commit"):
                publish_releases.validate_release_payload(
                    item,
                    root,
                    expected_repository_commit="trusted-commit",
                    expected_workflow_run_url="https://github.com/example/actions/runs/1",
                )


if __name__ == "__main__":
    unittest.main()
