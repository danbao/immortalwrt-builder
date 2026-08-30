import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_SCRIPT = REPOSITORY_ROOT / "files/usr/libexec/daed-maintenance"


class DaedMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "wing.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("create table sample(value text)")
            connection.execute("insert into sample values ('portable')")
            connection.commit()
        self.backups = self.root / "backups"
        self.state = self.root / "health.state"
        self.lock = self.root / "maintenance.lock"
        self.log = self.root / "logger.log"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self._fake("uci", "[ \"${FAKE_DAED_ENABLED:-1}\" = 1 ] && echo 1")
        self._fake("pidof", "[ \"${FAKE_HEALTH_OK:-0}\" = 1 ]")
        self._fake(
            "wget",
            "[ \"${FAKE_HEALTH_OK:-0}\" = 1 ] && printf '%s\\n' \"{\\\"data\\\":{\\\"numberUsers\\\":${FAKE_NUMBER_USERS:-1},\\\"healthCheck\\\":1}}\"",
        )
        self._fake(
            "jsonfilter",
            "[ \"${FAKE_HEALTH_OK:-0}\" = 1 ] || exit 1; case \"$*\" in *numberUsers*) echo \"${FAKE_NUMBER_USERS:-1}\" ;; *) echo 1 ;; esac",
        )
        self._fake("nslookup", "[ \"${FAKE_HEALTH_OK:-0}\" = 1 ]")
        self._fake("uclient-fetch", "[ \"${FAKE_HEALTH_OK:-0}\" = 1 ]")
        self._fake("logger", "printf '%s\\n' \"$*\" >> \"${FAKE_LOG:?}\"")
        self._fake("flock", "[ \"${FAKE_LOCK_HELD:-0}\" != 1 ]")
        self._fake("daed-init", "printf '%s\\n' \"$*\" >> \"${FAKE_INIT_LOG:?}\"")

    def _fake(self, name: str, body: str) -> None:
        path = self.fake_bin / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def _env(self, **overrides: str) -> dict[str, str]:
        return os.environ | {
            "DAED_MAINTENANCE_TEST_ROOT": str(self.root),
            "UCI_BIN": str(self.fake_bin / "uci"),
            "PIDOF_BIN": str(self.fake_bin / "pidof"),
            "WGET_BIN": str(self.fake_bin / "wget"),
            "JSONFILTER_BIN": str(self.fake_bin / "jsonfilter"),
            "NSLOOKUP_BIN": str(self.fake_bin / "nslookup"),
            "FETCH_BIN": str(self.fake_bin / "uclient-fetch"),
            "LOGGER_BIN": str(self.fake_bin / "logger"),
            "FLOCK_BIN": str(self.fake_bin / "flock"),
            "DAED_INIT": str(self.fake_bin / "daed-init"),
            "FAKE_INIT_LOG": str(self.root / "init.log"),
            "FAKE_LOG": str(self.log),
            **overrides,
        }

    def _run(
        self, command: str, *arguments: str, check: bool = True, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(MAINTENANCE_SCRIPT), command, *arguments],
            check=check,
            text=True,
            capture_output=True,
            env=self._env(**env),
        )

    def test_backup_is_consistent_and_rotates_to_seven_files(self) -> None:
        for _ in range(9):
            self._run("backup")
        backups = sorted(self.backups.glob("wing-*.db"))
        self.assertEqual(len(backups), 7)
        with closing(sqlite3.connect(backups[-1])) as connection:
            self.assertEqual(connection.execute("pragma quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("select value from sample").fetchone()[0], "portable")

        before = {path.name for path in backups}
        self.database.write_bytes(b"not a sqlite database")
        result = self._run("backup", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, {path.name for path in self.backups.glob("wing-*.db")})

    def test_health_warns_on_third_failure_and_logs_recovery(self) -> None:
        for _ in range(2):
            self._run("health", FAKE_HEALTH_OK="0")
        self.assertFalse(self.log.exists())
        self._run("health", FAKE_HEALTH_OK="0")
        self.assertIn("failed 3 consecutive times", self.log.read_text(encoding="utf-8"))
        self.assertIn("ai-connectivity", self.state.read_text(encoding="utf-8"))
        self._run("health", FAKE_HEALTH_OK="1")
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("recovered after 3 consecutive failures", log)
        self.assertFalse(self.state.exists())

    def test_restore_replaces_database_and_cycles_daed(self) -> None:
        self._run("backup")
        backup = next(self.backups.glob("wing-*.db"))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("update sample set value = 'changed'")
            connection.commit()

        self._run("restore", str(backup.resolve()))

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("select value from sample").fetchone()[0], "portable")
        self.assertEqual(
            (self.root / "init.log").read_text(encoding="utf-8").splitlines(),
            ["stop", "start"],
        )

    def test_restore_rejects_traversal_outside_backup_directory(self) -> None:
        self._run("backup")
        backup = next(self.backups.glob("wing-*.db"))
        outside = self.root / "wing-outside.db"
        outside.write_bytes(backup.read_bytes())
        traversal = self.backups / ".." / outside.name

        result = self._run("restore", str(traversal), check=False)

        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the managed backup directory", result.stderr)
        self.assertFalse((self.root / "init.log").exists())

    def test_disabled_daed_and_held_lock_are_noops(self) -> None:
        self._run("health", FAKE_DAED_ENABLED="0")
        self.assertFalse(self.state.exists())
        self._run("health", FAKE_HEALTH_OK="1", FAKE_NUMBER_USERS="0")
        self.assertFalse(self.state.exists())
        self._run("health", FAKE_HEALTH_OK="0", FAKE_LOCK_HELD="1")
        self.assertFalse(self.state.exists())

    def test_test_root_cannot_escape_through_a_symlink(self) -> None:
        link = self.root / "unsafe-link"
        link.symlink_to("/etc")
        result = subprocess.run(
            ["sh", str(MAINTENANCE_SCRIPT), "health"],
            check=False,
            text=True,
            capture_output=True,
            env=os.environ | {"DAED_MAINTENANCE_TEST_ROOT": str(link)},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe maintenance test root", result.stderr)


if __name__ == "__main__":
    unittest.main()
