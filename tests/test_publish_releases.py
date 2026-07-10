import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_releases


class PublishReleasesTests(unittest.TestCase):
    def test_expected_asset_paths_uses_dist_image_asset_by_default(self) -> None:
        item = {
            "ova_path": "dist/demo.ova",
            "checksum_path": "dist/demo.ova.sha256",
            "image_path": "build-out/immortalwrt-x86-64.img.gz",
            "image_asset": "immortalwrt-x86-64-20260710.img.gz",
        }
        ova, checksum, image = publish_releases.expected_asset_paths(item)
        self.assertEqual(ova, Path("dist/demo.ova"))
        self.assertEqual(checksum, Path("dist/demo.ova.sha256"))
        self.assertEqual(image, Path("dist/immortalwrt-x86-64-20260710.img.gz"))

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


if __name__ == "__main__":
    unittest.main()
