import argparse
import gzip
import json
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import openwrt_build_preflight as preflight


class OpenWrtBuildPreflightTests(unittest.TestCase):
    def test_daed_package_list_excludes_conflicting_proxy_stacks(self) -> None:
        package_file = Path(__file__).resolve().parents[1] / "config" / "openwrt-packages-daed.txt"
        packages = set(preflight.read_packages(package_file))
        self.assertIn("luci-app-daede", packages)
        self.assertNotIn("-shellsync", packages)
        self.assertIn("daed", packages)
        self.assertIn("luci-app-mosdns", packages)
        self.assertNotIn("luci-app-passwall2", packages)
        self.assertNotIn("luci-app-openclash", packages)
        self.assertNotIn("luci-app-nikki", packages)
        self.assertNotIn("luci-i18n-nikki-zh-cn", packages)
        self.assertNotIn("mihomo-meta", packages)

    def test_profiles_keep_required_remote_and_kms_services(self) -> None:
        config_dir = Path(__file__).resolve().parents[1] / "config"
        for filename in ("openwrt-packages.txt", "openwrt-packages-daed.txt"):
            with self.subTest(filename=filename):
                packages = set(preflight.read_packages(config_dir / filename))
                self.assertIn("luci-ssl", packages)
                self.assertIn("tailscale", packages)
                self.assertIn("luci-app-vlmcsd", packages)
                for package in (
                    "luci-app-zerotier",
                    "zerotier",
                    "luci-app-upnp",
                ):
                    self.assertNotIn(package, packages)

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

    def test_read_feeds_parses_explicit_verification_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            feed_file = Path(tmp_s) / "feeds.tsv"
            feed_file.write_text(
                "# name\turl\trequired\tverification\n"
                "nikki\thttps://example.test/feed\ttrue\tallow-untrusted\n",
                encoding="utf-8",
            )
            self.assertEqual(
                preflight.read_feeds(feed_file),
                [preflight.Feed("nikki", "https://example.test/feed", True, "allow-untrusted")],
            )

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

    def test_resolve_source_refs_uses_build_commit_and_pinned_feed_refs(self) -> None:
        build_commit = "a" * 40
        luci_commit = "b" * 40
        external_feed_commit = "c" * 40
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
            tmp = Path(tmp_s)
            components_path = tmp / "components.json"
            provenance_path = tmp / "provenance.json"
            feed_file = tmp / "feeds.tsv"
            components_path.write_text(json.dumps(components), encoding="utf-8")
            provenance_path.write_text('{"records": []}', encoding="utf-8")
            feed_file.write_text(
                "external\thttps://feed.example.test\ttrue\tallow-untrusted\t"
                "https://github.com/example/external-feed\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                preflight,
                "github_api_json",
                side_effect=[
                    {"sha": build_commit},
                    {"sha": external_feed_commit},
                ],
            ) as github_api:
                result = preflight.resolve_source_refs(
                    components_path=components_path,
                    feed_file=feed_file,
                    provenance_path=provenance_path,
                    feeds_buildinfo=(
                        "src-git luci "
                        f"https://github.com/immortalwrt/luci.git^{luci_commit}\n"
                    ),
                    immortalwrt_commit=build_commit[:12],
                    timeout=1,
                    retries=1,
                )

            refs = result["components"]
            self.assertEqual(
                refs["https://github.com/immortalwrt/immortalwrt"]["ref"],
                build_commit,
            )
            self.assertEqual(
                refs["https://github.com/immortalwrt/luci"]["ref"],
                luci_commit,
            )
            self.assertEqual(result["feeds"]["external"]["commit"], external_feed_commit)
            self.assertEqual(github_api.call_count, 2)

    def test_resolve_source_refs_allows_no_third_party_feed_file(self) -> None:
        build_commit = "a" * 40
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            components_path = tmp / "components.json"
            provenance_path = tmp / "provenance.json"
            components_path.write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "name": "core",
                                "source": "https://github.com/immortalwrt/immortalwrt",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            provenance_path.write_text('{"records": []}', encoding="utf-8")
            with mock.patch.object(
                preflight,
                "github_api_json",
                return_value={"sha": build_commit},
            ):
                result = preflight.resolve_source_refs(
                    components_path=components_path,
                    feed_file=None,
                    provenance_path=provenance_path,
                    feeds_buildinfo=(
                        "src-git packages "
                        f"https://github.com/immortalwrt/packages.git^{'b' * 40}\n"
                    ),
                    immortalwrt_commit=build_commit[:12],
                    timeout=1,
                    retries=1,
                )
            self.assertEqual(result["feeds"]["packages"]["commit"], "b" * 40)

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

    def test_imagebuilder_defaults_to_25_12_x86_generic(self) -> None:
        args = preflight.parse_args(["imagebuilder-info", "--version", "25.12.1"])
        self.assertEqual(args.target, "x86/generic")
        self.assertEqual(
            preflight.imagebuilder_archive_name(args.version, args.target),
            "immortalwrt-imagebuilder-25.12.1-x86-generic.Linux-x86_64.tar.zst",
        )

    def test_verify_release_asset_checks_api_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            asset = Path(tmp_s) / "demo.ipk"
            asset.write_bytes(b"trusted package")
            digest = preflight.sha256_file(asset)
            record = preflight.verify_release_asset(
                {"id": 7, "name": asset.name, "size": asset.stat().st_size, "digest": f"sha256:{digest}"},
                asset,
                allow_missing_digest=True,
            )
            self.assertEqual(record["verification_status"], "verified-api-digest")
            self.assertEqual(record["sha256"], digest)

            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                preflight.verify_release_asset(
                    {"id": 7, "name": asset.name, "size": asset.stat().st_size, "digest": f"sha256:{'0' * 64}"},
                    asset,
                    allow_missing_digest=True,
                )

    def test_verify_release_asset_marks_missing_digest_as_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            asset = Path(tmp_s) / "demo.ipk"
            asset.write_bytes(b"legacy package")
            record = preflight.verify_release_asset(
                {"id": 8, "name": asset.name, "size": asset.stat().st_size, "digest": None},
                asset,
                allow_missing_digest=True,
            )
            self.assertEqual(record["verification_status"], "unverified-upstream")
            with self.assertRaisesRegex(ValueError, "does not provide a digest"):
                preflight.verify_release_asset(
                    {"id": 8, "name": asset.name, "size": asset.stat().st_size, "digest": None},
                    asset,
                    allow_missing_digest=False,
                )

    def test_release_asset_snapshot_rejects_metadata_changes(self) -> None:
        release = {"tag_name": "v1", "id": 10}
        asset = {"id": 20, "name": "demo.ipk", "size": 3, "updated_at": "2026-07-01T00:00:00Z", "digest": None}
        preflight.ensure_release_asset_unchanged(release, asset, dict(release), dict(asset))
        changed = dict(asset, updated_at="2026-07-02T00:00:00Z")
        with self.assertRaisesRegex(RuntimeError, "changed during download"):
            preflight.ensure_release_asset_unchanged(release, asset, dict(release), changed)

    def test_parse_package_index_requires_license_source_and_hash(self) -> None:
        payload = (
            "Package: demo\n"
            "Version: 1.2.3\n"
            "License: MIT\n"
            "Source: feeds/packages/demo\n"
            "Filename: demo_1.2.3_x86_64.ipk\n"
            f"SHA256sum: {'a' * 64}\n"
            "Size: 123\n\n"
        )
        records = preflight.parse_package_index(payload, "https://example.test/feed")
        self.assertEqual(records[0]["package"], "demo")
        self.assertEqual(records[0]["license"], "MIT")
        self.assertEqual(records[0]["download_url"], "https://example.test/feed/demo_1.2.3_x86_64.ipk")

        with self.assertRaisesRegex(ValueError, "missing License"):
            preflight.parse_package_index(payload.replace("License: MIT\n", ""), "https://example.test/feed")

    def test_bounded_gzip_decompress_rejects_expansion_over_limit(self) -> None:
        payload = gzip.compress(b"a" * 1024)
        self.assertEqual(preflight.bounded_gzip_decompress(payload, max_bytes=1024), b"a" * 1024)
        with self.assertRaisesRegex(ValueError, "decompressed size limit"):
            preflight.bounded_gzip_decompress(payload, max_bytes=1023)

    def test_mirror_feed_hash_verifies_packages_under_untrusted_key_exception(self) -> None:
        package_bytes = b"verified package"
        digest = preflight.hashlib.sha256(package_bytes).hexdigest()
        manifest = (
            "Package: demo\n"
            "Version: 1.2.3\n"
            "Filename: demo_1.2.3_x86_64.ipk\n"
            f"SHA256sum: {digest}\n"
            f"Size: {len(package_bytes)}\n\n"
        ).encode()
        packages = gzip.compress(manifest)
        signature = b"untrusted upstream signature"

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)

            def fake_download(_url: str, target: Path, **_kwargs: object) -> None:
                target.write_bytes(package_bytes)

            with (
                mock.patch.object(
                    preflight,
                    "fetch_bytes",
                    side_effect=[packages, manifest, signature, packages, manifest, signature],
                ),
                mock.patch.object(preflight, "download_url", side_effect=fake_download),
            ):
                preflight.mirror_feed(
                    preflight.Feed("demo", "https://example.test/feed", True, "allow-untrusted"),
                    output_dir=tmp / "packages",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                    timeout=1,
                    retries=1,
                )

            provenance = json.loads((tmp / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(
                provenance["records"][0]["verification_status"],
                "hash-verified-packages-untrusted-signing-key",
            )
            self.assertEqual((tmp / "packages" / "demo_1.2.3_x86_64.ipk").read_bytes(), package_bytes)

    def test_mirror_feed_rejects_package_hash_mismatch_and_metadata_changes(self) -> None:
        package_bytes = b"expected package"
        digest = preflight.hashlib.sha256(package_bytes).hexdigest()
        manifest = (
            "Package: demo\n"
            "Version: 1.2.3\n"
            "Filename: demo_1.2.3_x86_64.ipk\n"
            f"SHA256sum: {digest}\n"
            f"Size: {len(package_bytes)}\n\n"
        ).encode()
        feed = preflight.Feed("demo", "https://example.test/feed", True, "allow-untrusted")

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)

            def corrupt_download(_url: str, target: Path, **_kwargs: object) -> None:
                target.write_bytes(b"corrupt package!")

            with (
                mock.patch.object(
                    preflight,
                    "fetch_bytes",
                    side_effect=[gzip.compress(manifest), manifest, b"signature"],
                ),
                mock.patch.object(preflight, "download_url", side_effect=corrupt_download),
                self.assertRaisesRegex(ValueError, "sha256 mismatch"),
            ):
                preflight.mirror_feed(
                    feed,
                    output_dir=tmp / "packages",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                    timeout=1,
                    retries=1,
                )

        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)

            def valid_download(_url: str, target: Path, **_kwargs: object) -> None:
                target.write_bytes(package_bytes)

            with (
                mock.patch.object(
                    preflight,
                    "fetch_bytes",
                    side_effect=[
                        gzip.compress(manifest),
                        manifest,
                        b"signature",
                        gzip.compress(manifest + b"\n"),
                        manifest,
                        b"signature",
                    ],
                ),
                mock.patch.object(preflight, "download_url", side_effect=valid_download),
                self.assertRaisesRegex(RuntimeError, "changed while mirroring"),
            ):
                preflight.mirror_feed(
                    feed,
                    output_dir=tmp / "packages",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                    timeout=1,
                    retries=1,
                )

    def test_mirror_feed_rejects_manifest_package_index_mismatch(self) -> None:
        manifest = (
            "Package: demo\nVersion: 1\nFilename: demo.ipk\n"
            f"SHA256sum: {'a' * 64}\nSize: 1\n\n"
        ).encode()
        package_index = manifest.replace(b"Version: 1", b"Version: 2")
        with tempfile.TemporaryDirectory() as tmp_s, mock.patch.object(
            preflight,
            "fetch_bytes",
            side_effect=[gzip.compress(package_index), manifest, b"signature"],
        ), self.assertRaisesRegex(RuntimeError, "does not match"):
            tmp = Path(tmp_s)
            preflight.mirror_feed(
                preflight.Feed("demo", "https://example.test/feed", True, "allow-untrusted"),
                output_dir=tmp / "packages",
                package_index=tmp / "package-index.json",
                provenance=tmp / "provenance.json",
                timeout=1,
                retries=1,
            )

    def test_collect_apk_package_index_uses_signed_imagebuilder_repositories(self) -> None:
        package = {
            "name": "demo",
            "version": "1.2.3-r1",
            "license": "MIT",
            "download-url": "https://downloads.example/packages/i386_pentium4/luci/demo.apk",
            "file-size": 123,
            "origin": "feeds/luci/applications/demo",
        }
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            apk_bin = tmp / "apk"
            repositories = tmp / "repositories"
            keys_dir = tmp / "keys"
            apk_bin.write_text("binary", encoding="utf-8")
            repositories.write_text("https://downloads.example/packages.adb\n", encoding="utf-8")
            keys_dir.mkdir()
            completed = preflight.subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps([package]), stderr=""
            )
            with mock.patch.object(
                preflight.subprocess,
                "run",
                side_effect=[preflight.subprocess.CompletedProcess(args=[], returncode=0), completed],
            ) as run:
                preflight.collect_apk_package_index(
                    apk_bin,
                    repositories,
                    keys_dir,
                    "i386_pentium4",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                )

            self.assertEqual(run.call_count, 2)
            command = run.call_args_list[1].args[0]
            self.assertIn("--keys-dir", command)
            self.assertNotIn("--allow-untrusted", command)
            self.assertNotIn("--usermode", command)
            self.assertIn("--usermode", run.call_args_list[0].args[0])
            self.assertIn("--arch", run.call_args_list[0].args[0])
            records = json.loads((tmp / "package-index.json").read_text(encoding="utf-8"))["packages"]
            self.assertEqual(records[0]["source_path"], "feeds/luci/applications/demo")
            provenance = json.loads((tmp / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(
                provenance["records"][0]["verification_status"],
                "verified-by-imagebuilder-apk-signing-keys",
            )

    def test_collect_apk_package_index_rejects_invalid_inputs_and_metadata(self) -> None:
        valid = {
            "name": "demo",
            "version": "1-r1",
            "license": "MIT",
            "download-url": "https://downloads.example/demo.apk",
            "file-size": 1,
            "origin": "feeds/packages/demo",
        }
        invalid_payloads = (
            ("not-json", "Expecting value"),
            ("[]", "returned no packages"),
            ('["not-an-object"]', "invalid package record"),
            (json.dumps([{**valid, "name": ""}]), "metadata is incomplete"),
            (json.dumps([{**valid, "download-url": "http://downloads.example/demo.apk"}]), "non-HTTPS"),
            (json.dumps([{**valid, "file-size": "invalid"}]), "invalid literal"),
        )
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            apk_bin = tmp / "apk"
            repositories = tmp / "repositories"
            keys_dir = tmp / "keys"
            apk_bin.write_text("binary", encoding="utf-8")
            repositories.write_text("https://downloads.example/packages.adb\n", encoding="utf-8")
            keys_dir.mkdir()

            for payload, message in invalid_payloads:
                with self.subTest(message=message), mock.patch.object(
                    preflight.subprocess,
                    "run",
                    side_effect=[
                        preflight.subprocess.CompletedProcess(args=[], returncode=0),
                        preflight.subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=payload, stderr=""
                        ),
                    ],
                ), self.assertRaisesRegex(ValueError, message):
                    preflight.collect_apk_package_index(
                        apk_bin,
                        repositories,
                        keys_dir,
                        "i386_pentium4",
                        package_index=tmp / "package-index.json",
                        provenance=tmp / "provenance.json",
                    )

            failed_init = preflight.subprocess.CompletedProcess(
                args=[], returncode=99, stdout="", stderr="signature rejected"
            )
            with mock.patch.object(
                preflight.subprocess, "run", return_value=failed_init
            ), self.assertRaisesRegex(RuntimeError, "signature rejected"):
                preflight.collect_apk_package_index(
                    apk_bin,
                    repositories,
                    keys_dir,
                    "i386_pentium4",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                )

            failed_query = preflight.subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="repository rejected"
            )
            with mock.patch.object(
                preflight.subprocess,
                "run",
                side_effect=[
                    preflight.subprocess.CompletedProcess(args=[], returncode=0),
                    failed_query,
                ],
            ), self.assertRaisesRegex(RuntimeError, "repository rejected"):
                preflight.collect_apk_package_index(
                    apk_bin,
                    repositories,
                    keys_dir,
                    "i386_pentium4",
                    package_index=tmp / "package-index.json",
                    provenance=tmp / "provenance.json",
                )

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
            self.assertIn(
                "immortalwrt-x86-64-20260713.img.gz",
                (dist / "immortalwrt-x86-64-20260713.img.gz.sha256").read_text(encoding="utf-8"),
            )

    def test_safe_extract_tar_rejects_parent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            archive_path = tmp / "unsafe.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("../escaped.ipk")
                payload = b"malicious"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            with self.assertRaises((tarfile.TarError, ValueError)):
                preflight.safe_extract_tar(archive_path, tmp / "extract")
            self.assertFalse((tmp / "escaped.ipk").exists())


if __name__ == "__main__":
    unittest.main()
