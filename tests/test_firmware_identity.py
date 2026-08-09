import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_firmware_identity as identity


class FirmwareIdentityTests(unittest.TestCase):
    def test_identity_is_deterministic_and_sensitive_to_build_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            manifest = tmp / "packages.manifest"
            provenance = tmp / "upstream-provenance.json"
            source_refs = tmp / "source-refs.json"
            manifest.write_text("daed - 1\nluci - 2\n", encoding="utf-8")
            provenance.write_text('{"source":"verified"}\n', encoding="utf-8")
            source_refs.write_text('{"commit":"abc"}\n', encoding="utf-8")

            first = identity.build_identity(
                flavor="daed",
                target="x86/64",
                builder_commit="a" * 40,
                imagebuilder_version="25.12.1",
                imagebuilder_sha256="b" * 64,
                immortalwrt_version_code="r1-cdef",
                immortalwrt_commit="cdef",
                package_manifest=manifest,
                provenance=provenance,
                source_refs=source_refs,
            )
            second = identity.build_identity(
                flavor="daed",
                target="x86/64",
                builder_commit="a" * 40,
                imagebuilder_version="25.12.1",
                imagebuilder_sha256="b" * 64,
                immortalwrt_version_code="r1-cdef",
                immortalwrt_commit="cdef",
                package_manifest=manifest,
                provenance=provenance,
                source_refs=source_refs,
            )
            self.assertEqual(first, second)
            self.assertRegex(first["identity_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(first["flavor"], "daed")
            self.assertEqual(first["target"], "x86/64")

            manifest.write_text("daed - 2\nluci - 2\n", encoding="utf-8")
            changed = identity.build_identity(
                flavor="daed",
                target="x86/64",
                builder_commit="a" * 40,
                imagebuilder_version="25.12.1",
                imagebuilder_sha256="b" * 64,
                immortalwrt_version_code="r1-cdef",
                immortalwrt_commit="cdef",
                package_manifest=manifest,
                provenance=provenance,
                source_refs=source_refs,
            )
            self.assertNotEqual(first["identity_sha256"], changed["identity_sha256"])

    def test_identity_rejects_invalid_hashes_and_flavors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            path = Path(tmp_s) / "input"
            path.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "flavor"):
                identity.build_identity(
                    flavor="other",
                    target="x86/64",
                    builder_commit="a" * 40,
                    imagebuilder_version="25.12.1",
                    imagebuilder_sha256="b" * 64,
                    immortalwrt_version_code="r1-cdef",
                    immortalwrt_commit="cdef",
                    package_manifest=path,
                    provenance=path,
                    source_refs=path,
                )
            for target, builder_commit, imagebuilder_sha256, expected in (
                ("arm64", "a" * 40, "b" * 64, "target"),
                ("x86/64", "short", "b" * 64, "builder commit"),
                ("x86/64", "a" * 40, "bad", "SHA256"),
            ):
                with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                    identity.build_identity(
                        flavor="daed",
                        target=target,
                        builder_commit=builder_commit,
                        imagebuilder_version="25.12.1",
                        imagebuilder_sha256=imagebuilder_sha256,
                        immortalwrt_version_code="r1-cdef",
                        immortalwrt_commit="cdef",
                        package_manifest=path,
                        provenance=path,
                        source_refs=path,
                    )
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                identity.build_identity(
                    flavor="daed",
                    target="x86/64",
                    builder_commit="a" * 40,
                    imagebuilder_version="25.12.1",
                    imagebuilder_sha256="b" * 64,
                    immortalwrt_version_code="r1-cdef",
                    immortalwrt_commit="cdef",
                    package_manifest=path,
                    provenance=path,
                    source_refs=path,
                )


if __name__ == "__main__":
    unittest.main()
