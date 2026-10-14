from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import config
import migrate
import recover_ouedkniss_price_evidence
from database_safety import (
    DatabaseTargetError,
    PROTECTED_PRODUCTION_DATABASE_PATH,
    enforce_database_target,
    same_database_file,
)
from tests.test_foundation_v2 import make_legacy_database


class MigrationTargetPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.db = self.directory / "isolated.sqlite"
        make_legacy_database(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _call_migration(self, *args: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            result = migrate.main(list(args))
        return result, stderr.getvalue()

    def test_wrong_target_commands_fail_before_backup_registry_or_schema_changes(self) -> None:
        original_bytes = self.db.read_bytes()
        original_objects = None
        conn = sqlite3.connect(self.db)
        try:
            original_objects = tuple(conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ))
        finally:
            conn.close()

        commands = (
            ("status",),
            ("preflight", "--target", "1"),
            ("apply", "--target", "1"),
            ("rollback-001",),
            ("restore", "--backup", str(self.directory / "unused.bak")),
        )
        for command_args in commands:
            with self.subTest(command=command_args[0]):
                result, error = self._call_migration(
                    command_args[0], "--db", str(self.db), *command_args[1:]
                )
                self.assertEqual(result, 1)
                self.assertIn("must resolve to the protected production database", error)
                self.assertEqual(self.db.read_bytes(), original_bytes)
                self.assertEqual(
                    set(self.directory.glob(self.db.name + ".pre-migration-*.bak")), set()
                )
                conn = sqlite3.connect(self.db)
                try:
                    self.assertEqual(
                        tuple(conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")),
                        original_objects,
                    )
                    self.assertIsNone(conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
                    ).fetchone())
                finally:
                    conn.close()

    def test_environment_override_does_not_change_protected_identity(self) -> None:
        with patch.dict(os.environ, {"KRATZWERK_DB_PATH": str(self.db)}):
            self.assertEqual(
                config.PROTECTED_PRODUCTION_DATABASE_PATH,
                PROTECTED_PRODUCTION_DATABASE_PATH,
            )
            result, error = self._call_migration(
                "apply", "--db", str(self.db), "--target", "1"
            )
        self.assertEqual(result, 1)
        self.assertIn("must resolve to the protected production database", error)
        self.assertFalse((self.directory / "isolated.sqlite.pre-migration-v001.bak").exists())

    def test_relative_case_and_symlink_aliases_resolve_consistently(self) -> None:
        relative_production = Path(os.path.relpath(PROTECTED_PRODUCTION_DATABASE_PATH, Path.cwd()))
        self.assertTrue(same_database_file(relative_production, PROTECTED_PRODUCTION_DATABASE_PATH))
        case_variant = PROTECTED_PRODUCTION_DATABASE_PATH.with_name(
            PROTECTED_PRODUCTION_DATABASE_PATH.name.swapcase()
        )
        self.assertTrue(same_database_file(case_variant, PROTECTED_PRODUCTION_DATABASE_PATH))

        relative_isolated = Path(os.path.relpath(self.db, Path.cwd()))
        self.assertTrue(same_database_file(relative_isolated, self.db))
        link = self.directory / "isolated-alias.sqlite"
        try:
            link.symlink_to(self.db)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation is unavailable on this Windows setup: {exc}")
        self.assertTrue(same_database_file(link, self.db))
        result, error = self._call_migration("apply", "--db", str(link), "--target", "1")
        self.assertEqual(result, 1)
        self.assertIn("must resolve to the protected production database", error)
        self.assertFalse((self.directory / "isolated.sqlite.pre-migration-v001.bak").exists())

        production_link = self.directory / "protected-production-alias.sqlite"
        try:
            production_link.symlink_to(PROTECTED_PRODUCTION_DATABASE_PATH)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Production target symlink creation is unavailable: {exc}")
        with self.assertRaisesRegex(DatabaseTargetError, "--isolated-db cannot target"):
            enforce_database_target(production_link, isolated=True)

    def test_isolated_opt_in_allows_fixture_migration_and_never_targets_production(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            result = migrate.main([
                "apply", "--db", str(self.db), "--target", "1", "--isolated-db"
            ])
        self.assertEqual(result, 0)
        self.assertTrue(list(self.directory.glob(self.db.name + ".pre-migration-v001.*.bak")))
        with self.assertRaisesRegex(DatabaseTargetError, "--isolated-db cannot target"):
            enforce_database_target(PROTECTED_PRODUCTION_DATABASE_PATH, isolated=True)

    def test_historical_recovery_refuses_canonical_production_even_when_env_is_overridden(self) -> None:
        stderr = io.StringIO()
        with patch.dict(os.environ, {"KRATZWERK_DB_PATH": str(self.db)}), \
                patch.object(config, "DATABASE_PATH", self.db), \
                contextlib.redirect_stderr(stderr):
            result = recover_ouedkniss_price_evidence.main([
                "--db", str(PROTECTED_PRODUCTION_DATABASE_PATH)
            ])
        self.assertEqual(result, 2)
        self.assertIn("protected production database cannot be used", stderr.getvalue())

    def test_historical_recovery_also_refuses_current_configured_database(self) -> None:
        stderr = io.StringIO()
        with patch.object(config, "DATABASE_PATH", self.db), contextlib.redirect_stderr(stderr):
            result = recover_ouedkniss_price_evidence.main(["--db", str(self.db)])
        self.assertEqual(result, 2)
        self.assertIn("separate isolated copy of the configured database", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
