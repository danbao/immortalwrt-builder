import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_img_to_ova


class OpenWrtImgToOvaTests(unittest.TestCase):
    def test_bypass_release_metadata_includes_builder_commit_in_tag_and_assets(self) -> None:
        tag, title, artifact = openwrt_img_to_ova.release_metadata(
            "immortalwrt-x86-64-bypass",
            "123abc456def",
            release_date="20260713",
            immortalwrt_commit="cf234f8de6d5",
            repository_commit="deadbeefcafebabe0123456789abcdef01234567",
        )
        self.assertEqual(
            tag,
            "openwrt-immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        )
        self.assertEqual(title, "ImmortalWrt x86_64 bypass ESXi OVA - 20260713 cf234f8de6d5 (deadbeefcafe)")
        self.assertEqual(
            artifact,
            "immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        )

    def test_published_tag_matches_same_image_and_builder_on_any_build_date(self) -> None:
        known_tags = [
            "openwrt-immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        ]
        self.assertEqual(
            openwrt_img_to_ova.find_published_tag(
                known_tags,
                "immortalwrt-x86-64-bypass",
                "123abc456def",
                "deadbeefcafebabe0123456789abcdef01234567",
            ),
            known_tags[0],
        )

    def test_published_tag_does_not_match_a_different_builder_commit(self) -> None:
        known_tags = [
            "openwrt-immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        ]
        self.assertIsNone(
            openwrt_img_to_ova.find_published_tag(
                known_tags,
                "immortalwrt-x86-64-bypass",
                "123abc456def",
                "0123456789abcdef0123456789abcdef01234567",
            )
        )

    def test_published_tag_does_not_match_a_different_image(self) -> None:
        known_tags = [
            "openwrt-immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        ]
        self.assertIsNone(
            openwrt_img_to_ova.find_published_tag(
                known_tags,
                "immortalwrt-x86-64-bypass",
                "fedcba987654",
                "deadbeefcafebabe0123456789abcdef01234567",
            )
        )

    def test_published_tag_without_builder_commit_ignores_builder_scoped_tags(self) -> None:
        known_tags = [
            "openwrt-immortalwrt-x86-64-bypass-20260713-cf234f8de6d5-deadbeefcafe-123abc456def",
        ]
        self.assertIsNone(
            openwrt_img_to_ova.find_published_tag(known_tags, "immortalwrt-x86-64-bypass", "123abc456def", None)
        )
        self.assertEqual(
            openwrt_img_to_ova.find_published_tag(
                ["openwrt-immortalwrt-x86-64-bypass-123abc456def"],
                "immortalwrt-x86-64-bypass",
                "123abc456def",
                None,
            ),
            "openwrt-immortalwrt-x86-64-bypass-123abc456def",
        )

    def test_read_known_tags_accepts_declared_list_and_rejects_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            path = Path(tmp_s) / "published-tags.json"
            path.write_text(json.dumps({"repository": "o/r", "tags": ["a", "b"]}), encoding="utf-8")
            self.assertEqual(openwrt_img_to_ova.read_known_tags(path), ["a", "b"])
            path.write_text(json.dumps({"tags": [1, 2]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "list of tag strings"):
                openwrt_img_to_ova.read_known_tags(path)

    def test_legacy_daed_release_metadata_still_resolves(self) -> None:
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

    def test_prepare_release_assets_declares_auditable_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            source_dir = tmp / "build-out"
            out_dir = tmp / "dist"
            source_dir.mkdir()
            out_dir.mkdir()
            image = source_dir / "immortalwrt-x86-64-bypass.img.gz"
            image.write_bytes(b"raw-image")
            ova = out_dir / "demo.ova"
            ova.write_bytes(b"ova")
            ova_checksum = out_dir / "demo.ova.sha256"
            ova_checksum.write_text("checksum  demo.ova\n", encoding="utf-8")
            manifest = source_dir / "official.manifest"
            manifest.write_text("daed - 1.27.0-r1\nluci-app-daed - 1.1.0-r2\n", encoding="utf-8")
            sbom = source_dir / "official.bom.cdx.json"
            sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
            buildinfo = source_dir / "config.buildinfo"
            buildinfo.write_text("CONFIG_SIGNED_PACKAGES=y\n", encoding="utf-8")
            profile = tmp / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "daed",
                        "profile": "generic",
                        "rootfs_partsize": 2048,
                        "nic_count": 1,
                        "required_packages": ["daed"],
                    }
                ),
                encoding="utf-8",
            )
            package_metadata = source_dir / "official-packages.json"
            package_metadata.write_text(
                json.dumps({"source": "official-immortalwrt", "packages": {"daed": "1.27.0-r1"}}),
                encoding="utf-8",
            )
            setup_wizard = tmp / "setup-openwrt.sh"
            setup_wizard.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            setup_wizard.chmod(0o755)
            results = tmp / "build-results.json"
            results.write_text(
                json.dumps(
                    {
                        "built": [
                            {
                                "release_tag": "openwrt-immortalwrt-x86-64-bypass-20260713-cf-123abc456def",
                                "release_title": "Demo",
                                "image_asset": "release.img.gz",
                                "image_path": str(image),
                                "image_sha256": "abc",
                                "ova_path": str(ova),
                                "checksum_path": str(ova_checksum),
                                "builder_version": "10",
                                "release_date": "20260713",
                                "immortalwrt_version_code": "r1-cf",
                                "immortalwrt_commit": "cf",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            openwrt_img_to_ova.prepare_release_assets(
                results_path=results,
                source_dir=source_dir,
                out_dir=out_dir,
                profile_path=profile,
                package_manifest=manifest,
                sbom=sbom,
                package_metadata=package_metadata,
                setup_wizard=setup_wizard,
                build_info_files=[buildinfo],
                provenance={
                    "imagebuilder_version": "25.12.1",
                    "imagebuilder_url": "https://downloads.example/imagebuilder.tar.zst",
                    "imagebuilder_sha256": "f" * 64,
                    "repository_commit": "deadbeef",
                    "workflow_run_url": "https://github.com/example/actions/runs/1",
                    "runner_type": "GitHub-hosted",
                },
            )

            payload = json.loads(results.read_text(encoding="utf-8"))
            assets = [Path(path) for path in payload["built"][0]["release_assets"]]
            self.assertEqual(
                {path.name for path in assets},
                {
                    "release.img.gz",
                    "demo.ova",
                    "demo.ova.sha256",
                    "release.manifest",
                    "release.bom.cdx.json",
                    "build-metadata.json",
                    "build-metadata.tar.gz",
                    "setup-openwrt.sh",
                    "SHA256SUMS",
                },
            )
            metadata = json.loads((out_dir / "build-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source"], "official-immortalwrt")
            self.assertEqual(metadata["packages"]["daed"], "1.27.0-r1")
            self.assertEqual(metadata["profile"]["rootfs_partsize"], 2048)
            self.assertEqual(metadata["profile"]["required_packages"], ["daed"])
            self.assertIn("release.img.gz", metadata["asset_sha256"])
            self.assertIn("setup-openwrt.sh", metadata["asset_sha256"])
            sums = (out_dir / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertIn("  build-metadata.json\n", sums)
            self.assertIn("  release.bom.cdx.json\n", sums)

            first_archive = (out_dir / "build-metadata.tar.gz").read_bytes()
            openwrt_img_to_ova.prepare_release_assets(
                results_path=results,
                source_dir=source_dir,
                out_dir=out_dir,
                profile_path=profile,
                package_manifest=manifest,
                sbom=sbom,
                package_metadata=package_metadata,
                setup_wizard=setup_wizard,
                build_info_files=[buildinfo],
                provenance=metadata["provenance"],
            )
            self.assertEqual(first_archive, (out_dir / "build-metadata.tar.gz").read_bytes())

    def test_prepare_release_assets_requires_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            results = tmp / "results.json"
            results.write_text('{"built": [{}]}', encoding="utf-8")
            with self.assertRaisesRegex(FileNotFoundError, "SBOM"):
                openwrt_img_to_ova.prepare_release_assets(
                    results_path=results,
                    source_dir=tmp,
                    out_dir=tmp / "dist",
                    profile_path=tmp / "profile.json",
                    package_manifest=tmp / "manifest",
                    sbom=tmp / "missing-sbom.json",
                    package_metadata=tmp / "packages.json",
                    setup_wizard=tmp / "setup-openwrt.sh",
                    build_info_files=[],
                    provenance={},
                )


if __name__ == "__main__":
    unittest.main()
