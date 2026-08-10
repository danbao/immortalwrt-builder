import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "files" / "usr" / "sbin" / "immortalwrt-updater"
PLAN = ROOT / "files" / "usr" / "libexec" / "immortalwrt-updater-plan.uc"
LUCI = ROOT / "files" / "www" / "luci-static" / "resources" / "view" / "system" / "immortalwrt-updater.js"
ACL = ROOT / "files" / "usr" / "share" / "rpcd" / "acl.d" / "immortalwrt-updater.json"


class UpdaterFileTests(unittest.TestCase):
    def write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def test_cli_requires_snapshot_confirmation_for_upgrade(self) -> None:
        result = subprocess.run(
            [UPDATER, "upgrade"],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--snapshot-confirmed", result.stderr)

    def test_updater_has_fixed_source_and_fail_closed_guards(self) -> None:
        content = UPDATER.read_text(encoding="utf-8")
        self.assertIn("danbao/immortalwrt-builder", content)
        self.assertIn("268435456", content)
        self.assertIn("sysupgrade -T", content)
        self.assertIn("sha256sum", content)
        self.assertNotIn("--force", content)

    def test_luci_and_rpc_acl_require_explicit_snapshot_confirmation(self) -> None:
        self.assertIn("snapshot_confirmed", LUCI.read_text(encoding="utf-8"))
        acl = json.loads(ACL.read_text(encoding="utf-8"))["immortalwrt-updater"]
        read_methods = acl["read"]["ubus"]["immortalwrt.updater"]
        write_methods = acl["write"]["ubus"]["immortalwrt.updater"]
        self.assertNotIn("upgrade", read_methods)
        self.assertIn("upgrade", write_methods)
        self.assertTrue(PLAN.is_file())

    def test_verify_rejects_checksum_mismatch_before_sysupgrade(self) -> None:
        result, sysupgrade_log = self.run_verify(checksum_matches=False, sysupgrade_exit=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum disagrees", result.stderr)
        self.assertFalse(sysupgrade_log.exists())

    def test_verify_rejects_sysupgrade_compatibility_failure(self) -> None:
        result, sysupgrade_log = self.run_verify(checksum_matches=True, sysupgrade_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sysupgrade rejected", result.stderr)
        self.assertEqual(sysupgrade_log.read_text(encoding="utf-8").strip(), "-T firmware.img.gz")

    def test_verify_fails_closed_for_incomplete_or_tampered_files(self) -> None:
        cases = (
            ({"missing": "checksum"}, "incomplete"),
            ({"image_digest_matches": False}, "SHA256 mismatch for firmware.img.gz"),
            ({"checksum_digest_matches": False}, "SHA256 mismatch for firmware.img.gz.sha256"),
            ({"size_override": 1}, "size changed"),
            ({"size_override": 268435457}, "outside the allowed range"),
        )
        for options, expected in cases:
            with self.subTest(expected=expected):
                result, sysupgrade_log = self.run_verify(
                    checksum_matches=True, sysupgrade_exit=0, **options
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse(sysupgrade_log.exists())

    def test_check_refresh_propagates_api_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            work = Path(tmp_s)
            fake_bin = work / "bin"
            fake_bin.mkdir()
            self.write_executable(fake_bin / "curl", "#!/bin/sh\nexit 22\n")
            result = self.run_cli(work, fake_bin, "check", "--refresh")
            self.assertNotEqual(result.returncode, 0)

    def test_download_rejects_insufficient_temporary_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            work = Path(tmp_s)
            fake_bin = work / "bin"
            fake_bin.mkdir()
            metadata_content = '{"release":{"image_sha256":"' + "a" * 64 + '"}}\n'
            metadata_sha = hashlib.sha256(metadata_content.encode()).hexdigest()
            self.write_executable(
                fake_bin / "curl",
                """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = '-o' ]; then
    output="$2"
    break
  fi
  shift
done
case "$output" in
  */releases.json) printf '%s\n' '[]' > "$output" ;;
  */build-metadata.json) printf '%s' "$FAKE_METADATA" > "$output" ;;
  *) exit 2 ;;
esac
""",
            )
            self.write_executable(
                fake_bin / "plan-helper",
                f"""#!/bin/sh
case "$1" in
  select) printf '%s\n' '{{"metadataUrl":"https://github.com/danbao/immortalwrt-builder/releases/download/test/build-metadata.json","metadataDigest":"sha256:{metadata_sha}"}}' ;;
  validate) printf '%s\n' '{{"updateAvailable":true,"imageSize":1024}}' ;;
  *) exit 2 ;;
esac
""",
            )
            self.write_executable(
                fake_bin / "jsonfilter",
                """#!/usr/bin/env python3
import json
import sys
source = sys.argv[sys.argv.index('-i') + 1]
path = sys.argv[sys.argv.index('-e') + 1].removeprefix('@.')
value = json.load(open(source, encoding='utf-8'))
for part in path.split('.'):
    value = value[part]
print(str(value).lower() if isinstance(value, bool) else value)
""",
            )
            self.write_executable(
                fake_bin / "df",
                "#!/bin/sh\nprintf '%s\n' 'Filesystem 1K-blocks Used Available Use% Mounted' 'tmpfs 1 0 1 0% /tmp'\n",
            )
            self.write_executable(fake_bin / "uci", "#!/bin/sh\nexit 1\n")
            result = self.run_cli(
                work,
                fake_bin,
                "download",
                extra_env={
                    "FAKE_METADATA": metadata_content,
                    "IMMORTALWRT_UPDATER_PLAN_HELPER": str(fake_bin / "plan-helper"),
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not enough temporary space", result.stderr)

    def run_cli(
        self,
        work: Path,
        fake_bin: Path,
        *arguments: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["IMMORTALWRT_UPDATER_WORK_DIR"] = str(work)
        env.update(extra_env or {})
        return subprocess.run(
            [UPDATER, *arguments], capture_output=True, text=True, check=False, env=env
        )

    def run_verify(
        self,
        *,
        checksum_matches: bool,
        sysupgrade_exit: int,
        missing: str | None = None,
        image_digest_matches: bool = True,
        checksum_digest_matches: bool = True,
        size_override: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        fake_bin = work / "bin"
        fake_bin.mkdir()
        image = work / "firmware.img.gz"
        checksum = work / "firmware.img.gz.sha256"
        metadata = work / "build-metadata.json"
        plan = work / "plan.json"
        sysupgrade_log = work / "sysupgrade.log"
        image.write_bytes(b"trusted test image")
        image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
        checksum_sha = image_sha if checksum_matches else "0" * 64
        checksum.write_text(f"{checksum_sha}  firmware.img.gz\n", encoding="utf-8")
        metadata.write_text(
            json.dumps({"release": {"image_sha256": image_sha}}), encoding="utf-8"
        )
        plan_image_sha = image_sha if image_digest_matches else "1" * 64
        checksum_digest = hashlib.sha256(checksum.read_bytes()).hexdigest()
        if not checksum_digest_matches:
            checksum_digest = "2" * 64
        plan.write_text(
            json.dumps(
                {
                    "imageSize": size_override if size_override is not None else image.stat().st_size,
                    "imageDigest": f"sha256:{plan_image_sha}",
                    "checksumDigest": f"sha256:{checksum_digest}",
                    "identity": "a" * 64,
                }
            ),
            encoding="utf-8",
        )
        self.write_executable(
            fake_bin / "jsonfilter",
            """#!/usr/bin/env python3
import json
import sys

source = sys.argv[sys.argv.index('-i') + 1]
path = sys.argv[sys.argv.index('-e') + 1].removeprefix('@.')
value = json.load(open(source, encoding='utf-8'))
for part in path.split('.'):
    value = value[part]
if isinstance(value, bool):
    print(str(value).lower())
else:
    print(value)
""",
        )
        self.write_executable(
            fake_bin / "sysupgrade",
            f"#!/bin/sh\nprintf '%s %s\\n' \"$1\" \"$(basename \"$2\")\" > \"{sysupgrade_log}\"\nexit {sysupgrade_exit}\n",
        )
        if missing == "checksum":
            checksum.unlink()
        result = self.run_cli(work, fake_bin, "verify")
        return result, sysupgrade_log


if __name__ == "__main__":
    unittest.main()
