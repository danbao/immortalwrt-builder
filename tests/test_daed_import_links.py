import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "files" / "usr" / "share" / "luci-app-daede" / "daed-import-links.uc"
UCODE_BIN = Path(os.environ.get("UCODE_BIN") or shutil.which("ucode") or "ucode")


@unittest.skipUnless(UCODE_BIN.is_file(), "OpenWrt host ucode is unavailable")
class DaedImportLinksTests(unittest.TestCase):
    def run_helper(self, payload: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp_s:
            source = Path(tmp_s) / "response.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [UCODE_BIN, HELPER, source],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_outputs_only_successfully_imported_node_links(self) -> None:
        result = self.run_helper(
            {
                "data": {
                    "importSubscription": {
                        "nodeImportResult": [
                            {"link": "ss://valid", "error": None, "node": {"id": "1"}},
                            {"link": "vmess://bad", "error": "unrecognized", "node": None},
                            {"link": "vless://valid", "error": None, "node": {"id": "2"}},
                        ]
                    }
                }
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["ss://valid", "vless://valid"])

    def test_rejects_empty_and_multiline_valid_results(self) -> None:
        empty = self.run_helper(
            {
                "data": {
                    "importSubscription": {
                        "nodeImportResult": [
                            {"link": "vmess://bad", "error": "unrecognized", "node": None}
                        ]
                    }
                }
            }
        )
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("no valid nodes", empty.stderr)

        multiline = self.run_helper(
            {
                "data": {
                    "importSubscription": {
                        "nodeImportResult": [
                            {"link": "ss://valid\nss://injected", "error": None, "node": {"id": "1"}}
                        ]
                    }
                }
            }
        )
        self.assertNotEqual(multiline.returncode, 0)
        self.assertIn("line break", multiline.stderr)


if __name__ == "__main__":
    unittest.main()
