import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "files" / "usr" / "libexec" / "immortalwrt-updater-plan.uc"
UCODE_BIN = Path(os.environ.get("UCODE_BIN") or shutil.which("ucode") or "ucode")


@unittest.skipUnless(UCODE_BIN.is_file(), "OpenWrt host ucode is unavailable")
class UpdaterPlanTests(unittest.TestCase):
    def run_helper(self, *arguments: Path | str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [UCODE_BIN, HELPER, *(str(argument) for argument in arguments)],
            capture_output=True,
            text=True,
            check=False,
        )

    def release_fixture(self, *, oversized: bool = False, metadata_digest: str | None = None) -> tuple[dict, dict]:
        tag = "openwrt-immortalwrt-x86-64-daed-20260810-abcdef123456-123456789abc"
        image_name = "immortalwrt-x86-64-daed-20260810-abcdef123456-123456789abc.img.gz"
        image_sha = "b" * 64
        base = f"https://github.com/danbao/immortalwrt-builder/releases/download/{tag}/"
        release = {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "published_at": "2026-08-10T00:00:00Z",
            "assets": [
                {"name": "build-metadata.json", "digest": metadata_digest or f"sha256:{'a' * 64}", "size": 100, "browser_download_url": base + "build-metadata.json"},
                {"name": image_name, "digest": f"sha256:{image_sha}", "size": 268435457 if oversized else 1024, "browser_download_url": base + image_name},
                {"name": image_name + ".sha256", "digest": f"sha256:{'c' * 64}", "size": 128, "browser_download_url": base + image_name + ".sha256"},
            ],
        }
        metadata = {
            "schema_version": 2,
            "repository": "danbao/immortalwrt-builder",
            "flavor": "daed",
            "target": "x86/64",
            "immortalwrt": {"version_code": "r1-abcdef123456"},
            "firmware_identity": {
                "repository": "danbao/immortalwrt-builder",
                "flavor": "daed",
                "target": "x86/64",
                "identity_sha256": "d" * 64,
            },
            "release": {"release_tag": tag, "image_asset": image_name, "image_sha256": image_sha},
        }
        return release, metadata

    def test_select_ignores_standard_release_and_rejects_missing_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            release, _ = self.release_fixture()
            standard = {**release, "tag_name": release["tag_name"].replace("-daed", "")}
            releases = tmp / "releases.json"
            releases.write_text(json.dumps([standard, release]), encoding="utf-8")
            result = self.run_helper("select", releases)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["tag"], release["tag_name"])

            release["assets"][0]["digest"] = None
            releases.write_text(json.dumps([release]), encoding="utf-8")
            rejected = self.run_helper("select", releases)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("missing or untrusted", rejected.stderr)

    def test_validate_rejects_oversized_asset_and_wrong_flavor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            releases = tmp / "releases.json"
            metadata_path = tmp / "metadata.json"
            release, metadata = self.release_fixture(oversized=True)
            releases.write_text(json.dumps([release]), encoding="utf-8")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            oversized = self.run_helper("validate", releases, metadata_path)
            self.assertNotEqual(oversized.returncode, 0)
            self.assertIn("oversized", oversized.stderr)

            release, metadata = self.release_fixture()
            metadata["flavor"] = "standard"
            releases.write_text(json.dumps([release]), encoding="utf-8")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            wrong_flavor = self.run_helper("validate", releases, metadata_path)
            self.assertNotEqual(wrong_flavor.returncode, 0)
            self.assertIn("trusted daed", wrong_flavor.stderr)

    def test_invalid_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            releases = Path(tmp_s) / "releases.json"
            releases.write_text("not json", encoding="utf-8")
            result = self.run_helper("select", releases)
            self.assertNotEqual(result.returncode, 0)

    def test_identity_comparison_and_published_at_prevent_false_or_old_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            releases = tmp / "releases.json"
            metadata_path = tmp / "metadata.json"
            release, metadata = self.release_fixture()
            releases.write_text(json.dumps([release]), encoding="utf-8")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            identity = metadata["firmware_identity"]["identity_sha256"]

            same = self.run_helper("validate", releases, metadata_path, "", identity)
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertFalse(json.loads(same.stdout)["updateAvailable"])

            different = self.run_helper("validate", releases, metadata_path, "", "e" * 64)
            self.assertEqual(different.returncode, 0, different.stderr)
            self.assertTrue(json.loads(different.stdout)["updateAvailable"])

            downgrade = self.run_helper(
                "validate", releases, metadata_path, "2026-08-11T00:00:00Z", "e" * 64
            )
            self.assertNotEqual(downgrade.returncode, 0)
            self.assertIn("older", downgrade.stderr)


if __name__ == "__main__":
    unittest.main()
