import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_build_preflight as preflight


class OpenWrtBuildPreflightTests(unittest.TestCase):
    def test_parse_feeds_buildinfo_requires_commit_pinned_sources(self) -> None:
        commit = "a" * 40
        feeds = preflight.parse_feeds_buildinfo(
            f"src-git packages https://github.com/immortalwrt/packages.git^{commit}\n"
        )
        self.assertEqual(feeds["packages"]["commit"], commit)
        self.assertIn(commit, feeds["packages"]["archive_url"])
        with self.assertRaisesRegex(ValueError, "contains no pinned feeds"):
            preflight.parse_feeds_buildinfo(
                "src-git packages https://github.com/immortalwrt/packages.git\n"
            )

    def test_resolve_source_refs_uses_only_official_pinned_refs(self) -> None:
        build_commit = "a" * 40
        luci_commit = "b" * 40
        components = {
            "components": [
                {
                    "name": "core",
                    "source": "https://github.com/immortalwrt/immortalwrt",
                },
                {
                    "name": "luci",
                    "source": "https://github.com/immortalwrt/luci",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp_s:
            components_path = Path(tmp_s) / "components.json"
            components_path.write_text(json.dumps(components), encoding="utf-8")
            with mock.patch.object(
                preflight,
                "github_api_json",
                return_value={"sha": build_commit},
            ) as github_api:
                result = preflight.resolve_source_refs(
                    components_path=components_path,
                    feeds_buildinfo=(
                        "src-git luci "
                        f"https://github.com/immortalwrt/luci.git^{luci_commit}\n"
                    ),
                    immortalwrt_commit=build_commit[:12],
                    timeout=1,
                    retries=1,
                )

        self.assertEqual(github_api.call_count, 1)
        self.assertEqual(
            result["components"]["https://github.com/immortalwrt/immortalwrt"]["ref"],
            build_commit,
        )
        self.assertEqual(
            result["components"]["https://github.com/immortalwrt/luci"]["ref"],
            luci_commit,
        )

    def test_resolve_source_refs_rejects_unpinned_component_sources(self) -> None:
        build_commit = "a" * 40
        with tempfile.TemporaryDirectory() as tmp_s:
            components_path = Path(tmp_s) / "components.json"
            components_path.write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "name": "external",
                                "source": "https://github.com/example/external",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                preflight,
                "github_api_json",
                return_value={"sha": build_commit},
            ), self.assertRaisesRegex(ValueError, "not pinned by official build metadata"):
                preflight.resolve_source_refs(
                    components_path=components_path,
                    feeds_buildinfo=(
                        "src-git packages "
                        f"https://github.com/immortalwrt/packages.git^{'b' * 40}\n"
                    ),
                    immortalwrt_commit=build_commit[:12],
                    timeout=1,
                    retries=1,
                )

    def test_parse_sha256sums_requires_one_archive_entry(self) -> None:
        archive = "immortalwrt-imagebuilder-25.12.1-x86-generic.Linux-x86_64.tar.zst"
        payload = f"{'a' * 64}  {archive}\n"
        self.assertEqual(preflight.parse_sha256sums(payload, archive), "a" * 64)
        with self.assertRaisesRegex(ValueError, "found 0"):
            preflight.parse_sha256sums(payload, "missing.tar.zst")

    def test_imagebuilder_defaults_to_25_12_x86_generic(self) -> None:
        args = preflight.parse_args(
            ["imagebuilder-info", "--version", "25.12.1"]
        )
        self.assertEqual(args.target, "x86/generic")
        self.assertEqual(
            preflight.imagebuilder_archive_name("25.12.1", args.target),
            "immortalwrt-imagebuilder-25.12.1-x86-generic.Linux-x86_64.tar.zst",
        )

    def test_collect_apk_package_index_uses_signed_imagebuilder_repositories(self) -> None:
        package = {
            "name": "demo",
            "version": "1.2.3-r1",
            "license": "MIT",
            "download-url": "https://downloads.example/packages/i386_pentium4/demo.apk",
            "file-size": 123,
            "origin": "feeds/luci/applications/demo",
        }
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            apk_bin = tmp / "apk"
            repositories = tmp / "repositories"
            keys_dir = tmp / "keys"
            apk_bin.write_text("binary", encoding="utf-8")
            repositories.write_text(
                "https://downloads.example/packages.adb\n", encoding="utf-8"
            )
            keys_dir.mkdir()
            completed = preflight.subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps([package]), stderr=""
            )
            with mock.patch.object(
                preflight.subprocess,
                "run",
                side_effect=[
                    preflight.subprocess.CompletedProcess(args=[], returncode=0),
                    completed,
                ],
            ) as run:
                preflight.collect_apk_package_index(
                    apk_bin,
                    repositories,
                    keys_dir,
                    "i386_pentium4",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                )

            init_command = run.call_args_list[0].args[0]
            query_command = run.call_args_list[1].args[0]
            self.assertIn("--arch", init_command)
            self.assertIn("i386_pentium4", init_command)
            self.assertIn("--usermode", init_command)
            self.assertIn("--keys-dir", query_command)
            self.assertNotIn("--allow-untrusted", query_command)
            records = json.loads(
                (tmp / "package-index.json").read_text(encoding="utf-8")
            )["packages"]
            self.assertEqual(records[0]["source_path"], "feeds/luci/applications/demo")
            provenance = json.loads(
                (tmp / "provenance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                provenance["records"][0]["verification_status"],
                "verified-by-imagebuilder-apk-signing-keys",
            )

    def test_collect_apk_package_index_rejects_incomplete_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            missing = Path(tmp_s)
            with self.assertRaisesRegex(ValueError, "inputs are incomplete"):
                preflight.collect_apk_package_index(
                    missing / "apk",
                    missing / "repositories",
                    missing / "keys",
                    "",
                    package_index=missing / "package-index.json",
                    provenance=missing / "provenance.json",
                )

    def test_copy_raw_images_handles_base_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            build_out = tmp / "build-out"
            dist = tmp / "dist"
            build_out.mkdir()
            image = build_out / "immortalwrt-x86-generic.img.gz"
            image.write_bytes(b"base")
            results = tmp / "build-results.json"
            results.write_text(
                json.dumps(
                    {
                        "built": [
                            {
                                "image_path": str(image),
                                "image_asset": "immortalwrt-x86-generic-20260713.img.gz",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                results=results,
                source_dir=build_out,
                out_dir=dist,
            )
            self.assertEqual(preflight.cmd_copy_raw_images(args), 0)
            self.assertEqual(
                (dist / "immortalwrt-x86-generic-20260713.img.gz").read_bytes(),
                b"base",
            )


if __name__ == "__main__":
    unittest.main()
