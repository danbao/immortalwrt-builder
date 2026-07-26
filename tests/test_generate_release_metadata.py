import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_release_metadata as metadata


class GenerateReleaseMetadataTests(unittest.TestCase):
    def test_release_item_for_flavor_uses_exact_tag_and_full_image_sha(self) -> None:
        results = {
            "built": [
                {
                    "release_tag": "openwrt-immortalwrt-x86-64-20260726-cf123-aaaaaaaaaaaa",
                    "image_sha256": "a" * 64,
                },
                {
                    "release_tag": "openwrt-immortalwrt-x86-64-daed-20260726-cf123-bbbbbbbbbbbb",
                    "image_sha256": "b" * 64,
                },
            ]
        }
        standard = metadata.release_item_for_flavor(results, "standard")
        daed = metadata.release_item_for_flavor(results, "daed")
        self.assertEqual(standard["image_sha256"], "a" * 64)
        self.assertIn(daed["release_tag"], metadata.spdx_namespace("danbao/immortalwrt-builder", daed))
        self.assertIn("b" * 64, metadata.spdx_namespace("danbao/immortalwrt-builder", daed))

    def test_parse_package_manifest_reads_name_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            manifest = Path(tmp_s) / "packages.manifest"
            manifest.write_text("luci - 25.123.1\ncurl - 8.10.1-r1\n", encoding="utf-8")
            self.assertEqual(
                metadata.parse_package_manifest(manifest),
                {"curl": "8.10.1-r1", "luci": "25.123.1"},
            )

    def test_load_components_rejects_missing_license_or_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            config = Path(tmp_s) / "components.json"
            config.write_text(
                json.dumps(
                    {
                        "components": [{"name": "broken", "license": "", "source": "https://example.test", "packages": ["*"]}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "license"):
                metadata.load_components(config)

            config.write_text(
                json.dumps(
                    {
                        "components": [{
                            "name": "broken path",
                            "license": "MIT",
                            "source": "https://example.test",
                            "source_path": "../outside",
                            "packages": ["*"],
                        }],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "source_path"):
                metadata.load_components(config)

            for invalid_component in (
                {
                    "name": "absolute path",
                    "license": "MIT",
                    "source": "https://example.test",
                    "source_path": "/absolute",
                    "packages": ["*"],
                },
                {
                    "name": "insecure upstream",
                    "license": "MIT",
                    "source": "https://example.test",
                    "upstream_source": "http://upstream.example.test",
                    "packages": ["*"],
                },
            ):
                config.write_text(
                    json.dumps({"components": [invalid_component]}),
                    encoding="utf-8",
                )
                with self.subTest(component=invalid_component["name"]):
                    with self.assertRaisesRegex(ValueError, "source_path|upstream_source"):
                        metadata.load_components(config)

    def test_component_for_package_uses_override_then_default(self) -> None:
        registry = {
            "components": [
                {
                    "name": "PassWall 2",
                    "license": "GPL-3.0-only",
                    "source": "https://github.com/Openwrt-Passwall/openwrt-passwall2",
                    "packages": ["luci-app-passwall2"],
                }
            ],
        }
        self.assertEqual(metadata.component_for_package("luci-app-passwall2", registry)["name"], "PassWall 2")
        self.assertIsNone(metadata.component_for_package("curl", registry))
        exact_source = (
            "https://github.com/Openwrt-Passwall/openwrt-passwall2/tree/v1.2.3"
        )
        self.assertEqual(
            metadata.exact_component_source(
                registry["components"][0],
                {
                    "components": {
                        registry["components"][0]["source"]: {"source": exact_source},
                    }
                },
            ),
            exact_source,
        )
        with self.assertRaisesRegex(ValueError, "missing exact source ref"):
            metadata.exact_component_source(registry["components"][0], {"components": {}})

    def test_generate_spdx_contains_both_flavors(self) -> None:
        registry = {"components": []}
        package_index = {
            ("luci", "1.0"): [{
                "license": "Apache-2.0",
                "source_path": "feeds/luci/luci",
                "download_url": "https://downloads.example/luci.ipk",
            }],
            ("daed", "2.0"): [{
                "license": "AGPL-3.0-only",
                "source_path": "feeds/packages/daed",
                "download_url": "https://downloads.example/daed.ipk",
            }],
        }
        source_refs = {
            "immortalwrt": {
                "source": f"https://github.com/immortalwrt/immortalwrt/tree/{'a' * 40}",
            },
            "feeds": {
                "luci": {"source": f"https://github.com/immortalwrt/luci/tree/{'b' * 40}"},
                "packages": {"source": f"https://github.com/immortalwrt/packages/tree/{'c' * 40}"},
            },
            "components": {},
        }
        document = metadata.generate_spdx(
            {"luci": "1.0", "daed": "2.0"},
            registry,
            package_index,
            source_refs,
            namespace="https://github.com/danbao/immortalwrt-builder/releases/test",
        )
        self.assertEqual(document["spdxVersion"], "SPDX-2.3")
        names = {package["name"] for package in document["packages"]}
        self.assertEqual(names, {"luci", "daed"})

    def test_conflicting_unused_index_records_only_fail_when_package_is_installed(self) -> None:
        registry = {"components": []}
        candidates = [
            {
                "license": "MIT",
                "source_path": "feeds/packages/demo",
                "download_url": "https://one.example/demo.ipk",
            },
            {
                "license": "MIT",
                "source_path": "feeds/custom/demo",
                "download_url": "https://two.example/demo.ipk",
            },
        ]
        source_refs = {
            "immortalwrt": {"source": f"https://github.com/example/core/tree/{'a' * 40}"},
            "feeds": {
                "packages": {"source": f"https://github.com/example/packages/tree/{'b' * 40}"},
                "custom": {"source": f"https://github.com/example/custom/tree/{'c' * 40}"},
            },
            "components": {},
        }
        metadata.generate_spdx(
            {"other": "1"},
            registry,
            {
                ("demo", "1"): candidates,
                ("other", "1"): [candidates[0]],
            },
            source_refs,
            namespace="https://example.test/spdx",
        )
        with self.assertRaisesRegex(ValueError, "missing unique exact package metadata"):
            metadata.generate_spdx(
                {"demo": "1"},
                registry,
                {("demo", "1"): candidates},
                source_refs,
                namespace="https://example.test/spdx",
            )

    def test_license_ref_requires_and_emits_extracted_text(self) -> None:
        source = "https://github.com/example/firmware"
        component = {
            "name": "Firmware",
            "license": "LicenseRef-Firmware",
            "license_text": "Redistribution is permitted with this notice.",
            "license_url": "https://example.test/LICENSE",
            "source": source,
            "source_path": "firmware/demo",
            "upstream_source": "https://upstream.example.test/tree/1",
            "version_pattern": "1.*",
            "packages": ["firmware"],
        }
        document = metadata.generate_spdx(
            {"firmware": "1.0"},
            {"components": [component]},
            {},
            {"components": {source: {"source": f"{source}/tree/{'a' * 40}"}}},
            namespace="https://example.test/spdx",
        )
        extracted = document["hasExtractedLicensingInfos"][0]
        self.assertEqual(extracted["licenseId"], "LicenseRef-Firmware")
        self.assertEqual(extracted["extractedText"], component["license_text"])
        self.assertEqual(
            document["packages"][0]["downloadLocation"],
            f"{source}/tree/{'a' * 40}/firmware/demo",
        )
        self.assertIn("upstream source:", document["packages"][0]["comment"])

        del component["license_text"]
        with self.assertRaisesRegex(ValueError, "missing extracted license text"):
            metadata.generate_spdx(
                {"firmware": "1.0"},
                {"components": [component]},
                {},
                {"components": {source: {"source": f"{source}/tree/{'a' * 40}"}}},
                namespace="https://example.test/spdx",
            )

        component["license_text"] = "Redistribution is permitted with this notice."
        with self.assertRaisesRegex(ValueError, "not covered by reviewed metadata"):
            metadata.generate_spdx(
                {"firmware": "2.0"},
                {"components": [component]},
                {},
                {"components": {source: {"source": f"{source}/tree/{'a' * 40}"}}},
                namespace="https://example.test/spdx",
            )


if __name__ == "__main__":
    unittest.main()
