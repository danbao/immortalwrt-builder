import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import update_release_records


class UpdateReleaseRecordsTests(unittest.TestCase):
    def test_retry_operation_stops_after_success(self) -> None:
        calls: list[int] = []

        def operation(attempt: int) -> None:
            calls.append(attempt)
            if attempt < 3:
                raise RuntimeError("push conflict")

        update_release_records.retry_operation(operation, attempts=3)
        self.assertEqual(calls, [1, 2, 3])

    def test_retry_operation_raises_last_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "attempt 3"):
            update_release_records.retry_operation(
                lambda attempt: (_ for _ in ()).throw(RuntimeError(f"attempt {attempt}")),
                attempts=3,
            )


if __name__ == "__main__":
    unittest.main()
