import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-host-ucode.sh"


class InstallHostUcodeTests(unittest.TestCase):
    def test_installer_requires_one_absolute_prefix(self) -> None:
        missing = subprocess.run(
            [INSTALLER], capture_output=True, text=True, check=False
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("usage", missing.stderr)

        relative = subprocess.run(
            [INSTALLER, "relative/path"], capture_output=True, text=True, check=False
        )
        self.assertNotEqual(relative.returncode, 0)
        self.assertIn("absolute", relative.stderr)


if __name__ == "__main__":
    unittest.main()
