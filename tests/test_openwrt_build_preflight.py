import argparse
import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_build_preflight as preflight


class OpenWrtBuildPreflightTests(unittest.TestCase):
    def test_repository_profile_selects_only_official_daed_packages(self) -> None:
        profile_path = Path(__file__).resolve().parents[1] / "config" / "build-profile.json"
        profile = preflight.load_build_profile(profile_path)

        self.assertEqual(profile["profile"], "generic")
        self.assertEqual(profile["rootfs_partsize"], 2048)
        self.assertEqual(profile["nic_count"], 1)
        packages = set(profile["packages"])
        self.assertTrue(
            {"daed", "daed-geoip", "daed-geosite", "luci-app-daed", "luci-i18n-daed-zh-cn"}
            <= packages
        )
        self.assertNotIn("luci-app-daede", packages)

    def test_repository_profile_contains_portable_runtime_baseline(self) -> None:
        profile_path = Path(__file__).resolve().parents[1] / "config" / "build-profile.json"
        profile = preflight.load_build_profile(profile_path)
        portable_packages = {
            "tailscale",
            "luci-app-tailscale-community",
            "luci-i18n-tailscale-community-zh-cn",
            "vnstat2",
            "vnstati2",
            "luci-app-vnstat2",
            "luci-i18n-vnstat2-zh-cn",
            "open-vm-tools",
            "sqlite3-cli",
        }
        self.assertLessEqual(portable_packages, set(profile["packages"]))
        self.assertLessEqual(portable_packages, set(profile["required_packages"]))

    def test_profile_rejects_duplicates_missing_required_and_forbidden_packages(self) -> None:
        base = {
            "schema_version": 1,
            "name": "test",
            "profile": "generic",
            "rootfs_partsize": 2048,
            "nic_count": 1,
            "image_glob": "*.img.gz",
            "packages": ["daed"],
            "required_packages": ["daed"],
            "forbidden_packages": ["luci-app-daede"],
        }
        with tempfile.TemporaryDirectory() as tmp_s:
            path = Path(tmp_s) / "profile.json"
            for packages, error in (
                (["daed", "daed"], "duplicate"),
                ([], "required package"),
                (["daed", "luci-app-daede"], "forbidden package"),
            ):
                payload = dict(base, packages=packages)
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    preflight.load_build_profile(path)

    def test_imagebuilder_config_requires_supply_chain_security_options(self) -> None:
        valid = "\n".join(
            [
                "CONFIG_SIGNED_PACKAGES=y",
                "CONFIG_SIGNATURE_CHECK=y",
                "CONFIG_DOWNLOAD_CHECK_CERTIFICATE=y",
                "CONFIG_JSON_OVERVIEW_IMAGE_INFO=y",
                "CONFIG_JSON_CYCLONEDX_SBOM=y",
            ]
        )
        preflight.validate_imagebuilder_config(valid)
        with self.assertRaisesRegex(ValueError, "CONFIG_SIGNATURE_CHECK"):
            preflight.validate_imagebuilder_config(valid.replace("CONFIG_SIGNATURE_CHECK=y", ""))

    def test_official_manifest_records_versions_and_rejects_forbidden_packages(self) -> None:
        profile = {
            "required_packages": ["daed", "daed-geoip", "luci-app-daed"],
            "forbidden_packages": ["luci-app-daede"],
        }
        manifest = "daed - 1.27.0-r1\ndaed-geoip - 20260101-r1\nluci-app-daed - 1.1.0-r2\n"
        packages = preflight.validate_package_manifest(manifest, profile)
        self.assertEqual(packages["daed"], "1.27.0-r1")
        self.assertEqual(packages["luci-app-daed"], "1.1.0-r2")

        with self.assertRaisesRegex(ValueError, "forbidden package"):
            preflight.validate_package_manifest(manifest + "luci-app-daede - 1.0-r1\n", profile)

    def test_validate_profile_command_exports_workflow_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            profile_path = tmp / "profile.json"
            env_path = tmp / "github.env"
            profile_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "test",
                        "profile": "generic",
                        "rootfs_partsize": 2048,
                        "nic_count": 1,
                        "image_glob": "*.img.gz",
                        "packages": ["daed"],
                        "required_packages": ["daed"],
                        "forbidden_packages": ["luci-app-daede"],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(config=profile_path, github_env=env_path)
            self.assertEqual(preflight.cmd_validate_profile(args), 0)
            values = env_path.read_text(encoding="utf-8")
            self.assertIn("BUILD_PROFILE=generic\n", values)
            self.assertIn("ROOTFS_PARTSIZE=2048\n", values)
            self.assertIn("BUILD_PACKAGES=daed\n", values)

    def test_validate_manifest_command_records_every_required_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            profile_path = tmp / "profile.json"
            manifest_path = tmp / "image.manifest"
            metadata_path = tmp / "official-packages.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "test",
                        "profile": "generic",
                        "rootfs_partsize": 2048,
                        "nic_count": 1,
                        "image_glob": "*.img.gz",
                        "packages": ["daed", "tailscale", "vnstat2"],
                        "required_packages": ["daed", "tailscale", "vnstat2"],
                        "forbidden_packages": ["luci-app-daede"],
                    }
                ),
                encoding="utf-8",
            )
            manifest_path.write_text(
                "daed - 1.27.0-r1\ntailscale - 1.98.3-r1\nvnstat2 - 2.13-r1\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                profile=profile_path,
                manifest=manifest_path,
                metadata_out=metadata_path,
            )

            self.assertEqual(preflight.cmd_validate_manifest(args), 0)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["packages"],
                {"daed": "1.27.0-r1", "tailscale": "1.98.3-r1", "vnstat2": "2.13-r1"},
            )

    def test_parse_sha256sums_requires_exactly_one_archive(self) -> None:
        filename = "immortalwrt-imagebuilder-25.12.1-x86-64.Linux-x86_64.tar.zst"
        expected = "a" * 64
        self.assertEqual(preflight.parse_sha256sums(f"{expected}  {filename}\n", filename), expected)
        with self.assertRaisesRegex(ValueError, "expected one sha256 entry"):
            preflight.parse_sha256sums("", filename)

    def test_collect_image_outputs_requires_one_image_and_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            target = tmp / "target"
            output = tmp / "out"
            target.mkdir()
            with self.assertRaisesRegex(ValueError, "exactly one image"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")

            first = target / "immortalwrt-25.12.1-x86-64-generic-squashfs-combined-efi.img.gz"
            second = target / "second.img.gz"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            with self.assertRaisesRegex(ValueError, "found 2"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")

            second.unlink()
            with self.assertRaisesRegex(ValueError, "non-empty string"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "  ")
            with self.assertRaisesRegex(FileNotFoundError, "manifest"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")

            (target / "immortalwrt-25.12.1-x86-64-generic.manifest").write_text(
                "daed - 1\n", encoding="utf-8"
            )
            (target / "immortalwrt-25.12.1-x86-64-generic.bom.cdx.json").write_text(
                "{}\n", encoding="utf-8"
            )

            duplicate_manifest = target / "duplicate.manifest"
            duplicate_manifest.write_text("other - 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one final image manifest"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")
            duplicate_manifest.unlink()

            duplicate_sbom = target / "duplicate.bom.cdx.json"
            duplicate_sbom.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one final image SBOM"):
                preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")
            duplicate_sbom.unlink()

            collected = preflight.collect_image_outputs(target, "*.img.gz", output, "immortalwrt-x86-64-bypass")
            self.assertEqual(collected["image"], output / "immortalwrt-x86-64-bypass.img.gz")
            self.assertEqual((output / "final-image.manifest").read_text(encoding="utf-8"), "daed - 1\n")


if __name__ == "__main__":
    unittest.main()
