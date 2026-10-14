from __future__ import annotations

import sqlite3
import json
import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import migrate
from migration_lifecycle import (
    MigrationError,
    apply_migrations,
    backup_database,
    inspect_database,
    migration_checksum,
    preflight_database,
    restore_verified_backup,
    source_fingerprints,
    verify_backup,
)
import migration_lifecycle
from database_safety import DatabaseTargetError
from price_evidence.recovery import recover_ouedkniss_price_evidence
from price_evidence.repository import (
    append_price_evidence_review,
    price_evidence_review_authorization_payload,
)
from tests.test_foundation_v2 import make_legacy_database


TEST_REVIEW_KEY = "isolated-test-review-key-not-for-production-0001"


def signed_review(conn: sqlite3.Connection, evidence_id: int, **kwargs) -> int:
    payload = price_evidence_review_authorization_payload(evidence_id, **kwargs)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(TEST_REVIEW_KEY.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    with patch.dict(os.environ, {"KRATZWERK_PRICE_REVIEW_HMAC_KEY": TEST_REVIEW_KEY}):
        return append_price_evidence_review(
            conn, evidence_id, authorization_signature=signature, **kwargs
        )


def independent_currency_evidence(code: str) -> dict:
    content = f"Independent fixture price is denominated in {code}."
    return {"currency_evidence": [{
        "source_url": "https://independent.example/legacy-price",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "currency_code": code,
    }]}


class MigrationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.db = self.directory / "isolated.sqlite"
        self.manifest_key = self.directory / "manifest-signing.key"
        self._manifest_key_patch = patch.object(
            migration_lifecycle, "_manifest_key_path", return_value=self.manifest_key
        )
        self._manifest_key_patch.start()
        migration_lifecycle.initialize_backup_manifest_signing_key()
        make_legacy_database(self.db)
        migrate.apply(self.db, isolated=True)

    def tearDown(self) -> None:
        self._manifest_key_patch.stop()
        self.temp.cleanup()

    def connect(self, path: Path | None = None) -> sqlite3.Connection:
        conn = sqlite3.connect(path or self.db)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def fingerprints(self) -> dict:
        conn = self.connect()
        try:
            return source_fingerprints(conn)
        finally:
            conn.close()

    def test_direct_backup_helper_rejects_wrong_target_before_creating_files(self) -> None:
        before = self.db.read_bytes()
        existing = set(self.directory.glob(self.db.name + ".pre-migration-*.bak*"))
        with patch.dict(os.environ, {"KRATZWERK_DB_PATH": str(self.db)}):
            with self.assertRaisesRegex(DatabaseTargetError, "must resolve to the protected production database"):
                backup_database(self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(set(self.directory.glob(self.db.name + ".pre-migration-*.bak*")), existing)

        alias = self.directory / "isolated-alias.sqlite"
        try:
            alias.symlink_to(self.db)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation is unavailable on this Windows setup: {exc}")
        with self.assertRaisesRegex(DatabaseTargetError, "must resolve to the protected production database"):
            backup_database(alias)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(set(self.directory.glob(self.db.name + ".pre-migration-*.bak*")), existing)

    def test_restore_requires_writer_acknowledgment_and_manifest_bound_target(self) -> None:
        backup = backup_database(self.db, migration_version=2, isolated=True)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(MigrationError, "writers-stopped acknowledgment"):
            restore_verified_backup(self.db, backup, isolated=True)
        self.assertEqual(self.db.read_bytes(), before)

        other = self.directory / "other.sqlite"
        make_legacy_database(other)
        with self.assertRaisesRegex(MigrationError, "different database target"):
            restore_verified_backup(other, backup, isolated=True, writers_stopped=True)
        self.assertTrue(other.is_file())
        self.assertEqual(self.db.read_bytes(), before)

    def test_restore_rejects_unrelated_healthy_backup_altered_content_and_bad_manifest(self) -> None:
        other = self.directory / "unrelated.sqlite"
        make_legacy_database(other)
        unrelated_backup = backup_database(other, migration_version=1, isolated=True)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(MigrationError, "different database target"):
            restore_verified_backup(self.db, unrelated_backup, isolated=True, writers_stopped=True)
        self.assertEqual(self.db.read_bytes(), before)

        unrelated_file = self.directory / "healthy-unmanifested.sqlite"
        conn = sqlite3.connect(unrelated_file)
        try:
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT)")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(MigrationError, "provenance manifest is missing"):
            restore_verified_backup(
                self.db, unrelated_file, isolated=True, writers_stopped=True
            )
        self.assertEqual(self.db.read_bytes(), before)

        backup = backup_database(self.db, migration_version=2, isolated=True)
        manifest_path = Path(str(backup) + ".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["intended_target"] = str(other.resolve())
        manifest["signature_hmac_sha256"] = migration_lifecycle._manifest_signature(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "target identity is inconsistent"):
            restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        manifest["intended_target"] = str(self.db.resolve())
        manifest["intended_target_identity"] = os.path.normcase(str(self.db.resolve()))
        manifest["signature_hmac_sha256"] = migration_lifecycle._manifest_signature(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        backup.write_bytes(backup.read_bytes() + b"tampered")
        with self.assertRaisesRegex(MigrationError, "content hash does not match"):
            restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        self.assertEqual(self.db.read_bytes(), before)

    def test_restore_preserves_existing_wal_and_shm_beside_previous_database(self) -> None:
        backup = backup_database(self.db, migration_version=2, isolated=True)
        wal = Path(str(self.db) + "-wal")
        shm = Path(str(self.db) + "-shm")
        wal.write_bytes(b"isolated-wal-sidecar")
        shm.write_bytes(b"isolated-shm-sidecar")
        result = restore_verified_backup(
            self.db, backup, isolated=True, writers_stopped=True
        )
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        preserved = {Path(item).name: Path(item) for item in result["preserved_wal_shm"]}
        self.assertEqual(len(preserved), 2)
        self.assertIn(b"isolated-wal-sidecar", [path.read_bytes() for path in preserved.values()])
        self.assertIn(b"isolated-shm-sidecar", [path.read_bytes() for path in preserved.values()])

    def test_restore_failure_at_copy_verification_and_replacement_preserves_target_and_sidecars(self) -> None:
        backup = backup_database(self.db, migration_version=2, isolated=True)
        before = self.db.read_bytes()
        wal = Path(str(self.db) + "-wal")
        shm = Path(str(self.db) + "-shm")
        wal.write_bytes(b"preserved-wal")
        shm.write_bytes(b"preserved-shm")
        sidecars_before = {wal: wal.read_bytes(), shm: shm.read_bytes()}

        def assert_pristine() -> None:
            self.assertEqual(self.db.read_bytes(), before)
            self.assertEqual({path: path.read_bytes() for path in sidecars_before}, sidecars_before)
            self.assertEqual(list(self.directory.glob(f".{self.db.name}.restore-stage-*.sqlite")), [])

        with patch.object(
            migration_lifecycle, "_copy_backup_to_restore_stage",
            side_effect=RuntimeError("injected stage copy failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "stage copy failure"):
                restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        assert_pristine()

        original_verify = migration_lifecycle.verify_backup

        def fail_staged_verification(path: Path, **kwargs):
            if ".restore-stage-" in Path(path).name:
                raise RuntimeError("injected staged verification failure")
            return original_verify(path, **kwargs)

        with patch.object(migration_lifecycle, "verify_backup", side_effect=fail_staged_verification):
            with self.assertRaisesRegex(RuntimeError, "staged verification failure"):
                restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        assert_pristine()

        real_replace = os.replace

        def fail_stage_install(source, destination):
            if ".restore-stage-" in Path(source).name and Path(destination) == self.db:
                raise PermissionError("injected target replacement failure")
            return real_replace(source, destination)

        with patch.object(migration_lifecycle.os, "replace", side_effect=fail_stage_install):
            with self.assertRaisesRegex(PermissionError, "target replacement failure"):
                restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        assert_pristine()

        def fail_final_verification(path: Path, **kwargs):
            if Path(path) == self.db:
                raise RuntimeError("injected post-install verification failure")
            return original_verify(path, **kwargs)

        with patch.object(migration_lifecycle, "verify_backup", side_effect=fail_final_verification):
            with self.assertRaisesRegex(RuntimeError, "post-install verification failure"):
                restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        assert_pristine()

    def test_restore_reports_exact_stage_path_when_cleanup_fails(self) -> None:
        backup = backup_database(self.db, migration_version=2, isolated=True)
        before = self.db.read_bytes()
        original_verify = migration_lifecycle.verify_backup

        def fail_staged_verification(path: Path, **kwargs):
            if ".restore-stage-" in Path(path).name:
                raise RuntimeError("injected verification failure")
            return original_verify(path, **kwargs)

        real_unlink = Path.unlink
        remaining: list[Path] = []

        def fail_stage_cleanup(path: Path, *args, **kwargs):
            if ".restore-stage-" in path.name:
                remaining.append(path)
                raise PermissionError("injected cleanup denial")
            return real_unlink(path, *args, **kwargs)

        with patch.object(migration_lifecycle, "verify_backup", side_effect=fail_staged_verification):
            with patch.object(Path, "unlink", fail_stage_cleanup):
                with self.assertRaises(MigrationError) as caught:
                    restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        self.assertEqual(len(remaining), 1)
        self.assertIn(str(remaining[0]), str(caught.exception))
        self.assertIn("injected cleanup denial", str(caught.exception))
        self.assertTrue(remaining[0].exists())
        self.assertEqual(self.db.read_bytes(), before)
        real_unlink(remaining[0])

    def test_missing_manifest_key_verification_fails_without_creating_replacement(self) -> None:
        backup = backup_database(self.db, migration_version=2, isolated=True)
        self.manifest_key.unlink()
        with self.assertRaisesRegex(MigrationError, "signing key is missing"):
            verify_backup(backup, require_manifest=True)
        self.assertFalse(self.manifest_key.exists())

        self.manifest_key.write_bytes(b"invalid-existing-key")
        invalid_key_before = self.manifest_key.read_bytes()
        with self.assertRaisesRegex(MigrationError, "signing key is invalid"):
            verify_backup(backup, require_manifest=True)
        self.assertEqual(self.manifest_key.read_bytes(), invalid_key_before)

        self.manifest_key.unlink()
        backups_before = set(self.directory.glob(self.db.name + ".pre-migration-*.bak*"))
        with self.assertRaisesRegex(MigrationError, "signing key is missing"):
            backup_database(self.db, migration_version=3, isolated=True)
        self.assertFalse(self.manifest_key.exists())
        self.assertEqual(
            set(self.directory.glob(self.db.name + ".pre-migration-*.bak*")), backups_before
        )

    def test_migration_003_failure_rolls_back_and_retry_preserves_sources(self) -> None:
        apply_migrations(self.db, target_version=2, isolated=True)
        source_before = self.fingerprints()
        statement_count = len(migration_lifecycle._sql_statements(
            migration_lifecycle.MIGRATIONS[3][1].read_text(encoding="utf-8")
        ))
        for failure_point in range(1, statement_count + 1):
            with self.subTest(statement=failure_point):
                with self.assertRaisesRegex(RuntimeError, "Injected migration failure"):
                    apply_migrations(
                        self.db, target_version=3,
                        fail_after_statement=failure_point, isolated=True,
                    )
                state = inspect_database(self.db)
                self.assertEqual(state["price_evidence_002"], "APPLIED")
                self.assertEqual(state["price_evidence_003"], "ABSENT")
                self.assertEqual([row["version"] for row in state["migrations"]], [1, 2])
                self.assertEqual(self.fingerprints(), source_before)

        run = apply_migrations(self.db, target_version=3, isolated=True)
        self.assertEqual(run.states["003_price_evidence_trust_policy.sql"], "APPLIED")
        self.assertEqual(inspect_database(self.db)["price_evidence_003"], "APPLIED")
        self.assertEqual(self.fingerprints(), source_before)

    def test_positive_legacy_amount_remains_currency_unknown_without_new_evidence(self) -> None:
        legacy = self.directory / "legacy-positive-price.sqlite"
        make_legacy_database(legacy)
        conn = sqlite3.connect(legacy)
        try:
            conn.execute("UPDATE Marketplace_Listings SET current_price=72000 WHERE id=10")
            conn.commit()
        finally:
            conn.close()

        apply_migrations(legacy, target_version=3, isolated=True)
        conn = sqlite3.connect(legacy, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            observation = conn.execute(
                "SELECT id,price,currency,source_payload_json FROM marketplace_listing_observations WHERE listing_id=10"
            ).fetchone()
            self.assertEqual(observation["price"], 72000.0)
            self.assertEqual(observation["currency"], "DZD")  # immutable Migration 001 record
            source_payload = json.loads(observation["source_payload_json"])
            self.assertEqual(source_payload["legacy_price_zero_treated_as_unknown"], False)
            assessment = conn.execute(
                "SELECT raw_legacy_amount,currency_code,currency_basis,source_evidence_json "
                "FROM legacy_currency_unconfirmed_observations WHERE observation_id=?",
                (observation["id"],),
            ).fetchone()
            self.assertEqual(tuple(assessment[:3]), (72000.0, None, "UNKNOWN"))
            self.assertEqual(json.loads(assessment[3])["migration"], "001_foundation_v2")

            recovery = recover_ouedkniss_price_evidence(conn)
            self.assertEqual(recovery["skipped"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT current_price FROM legacy_marketplace_listings WHERE id=10"
            ).fetchone()[0], 72000.0)
        finally:
            conn.close()

    def test_legacy_migrated_currency_requires_independent_review_to_become_trusted(self) -> None:
        legacy = self.directory / "legacy-positive-trust.sqlite"
        make_legacy_database(legacy)
        conn = sqlite3.connect(legacy)
        try:
            conn.execute("UPDATE Marketplace_Listings SET current_price=72000 WHERE id=10")
            conn.commit()
        finally:
            conn.close()
        apply_migrations(legacy, target_version=3, isolated=True)
        conn = sqlite3.connect(legacy)
        conn.row_factory = sqlite3.Row
        try:
            observation = conn.execute(
                "SELECT id,observed_at FROM marketplace_listing_observations WHERE listing_id=10"
            ).fetchone()
            evidence_id = conn.execute(
                """INSERT INTO price_evidence (
                    source_platform,source_entity_key,marketplace_observation_id,
                    source_observed_at,price_observed_at,raw_value_json,raw_price_text,
                    candidate_amount,source_unit,unit_status,currency_code,currency_basis,
                    price_type,product_identity_state,product_identity_key,extraction_method,
                    extraction_version,extracted_at,extraction_status,quality_status,
                    reason_codes_json,supporting_evidence_json,idempotency_key
                ) VALUES ('Ouedkniss','legacy-10',? ,?,?,'72000','72000',72000,
                    'ITEM','NOT_APPLICABLE','DZD','SOURCE_CONFIRMED','REGULAR','VERIFIED',
                    'verified:legacy-item','fixture','v1','2026-10-13T10:00:00Z','SUCCESS',
                    'VALID','[]','{}','legacy-unconfirmed-test')""",
                (observation["id"], observation["observed_at"], observation["observed_at"]),
            ).lastrowid
            signed_review(
                conn,
                evidence_id,
                decision="APPROVED",
                reviewer="fixture-reviewer",
                reviewed_at="2026-10-13T10:01:00Z",
                quality_status="VALID",
                unit_status="NOT_APPLICABLE",
                product_identity_state="VERIFIED",
                product_identity_key="verified:legacy-item",
                price_type="REGULAR",
            )
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)
            ).fetchone())
            signed_review(
                conn,
                evidence_id,
                decision="APPROVED",
                reviewer="independent-reviewer",
                reviewed_at="2026-10-13T10:02:00Z",
                quality_status="VALID",
                currency_code="DZD",
                currency_basis="INDEPENDENTLY_VERIFIED",
                unit_status="NOT_APPLICABLE",
                product_identity_state="VERIFIED",
                product_identity_key="verified:legacy-item",
                price_type="REGULAR",
                evidence=independent_currency_evidence("DZD"),
            )
            trusted = conn.execute(
                "SELECT currency_code,currency_basis FROM trusted_price_evidence WHERE price_evidence_id=?",
                (evidence_id,),
            ).fetchone()
            self.assertEqual(tuple(trusted), ("DZD", "INDEPENDENTLY_VERIFIED"))
        finally:
            conn.close()

    def test_verified_backup_and_migration_registry(self) -> None:
        before = self.fingerprints()
        backup = backup_database(self.db, migration_version=2, isolated=True)
        verification = verify_backup(backup, expected_fingerprints=before)
        self.assertEqual(verification["integrity_check"], "ok")
        self.assertEqual(verification["foreign_key_violations"], 0)

        run = apply_migrations(self.db, target_version=2, isolated=True)
        self.assertTrue(run.backups)
        self.assertEqual(self.fingerprints(), before)
        state = inspect_database(self.db)
        rows = {row["version"]: row for row in state["migrations"]}
        self.assertEqual(rows[1]["state"], "APPLIED")
        self.assertIsNotNone(rows[1]["applied_at"])
        self.assertEqual(rows[1]["checksum"], migration_checksum(1))
        self.assertEqual(rows[2]["state"], "APPLIED")
        self.assertIsNotNone(rows[2]["applied_at"])
        self.assertEqual(rows[2]["checksum"], migration_checksum(2))

    def test_injected_failure_rolls_back_ddl_and_retry_succeeds(self) -> None:
        source_before = self.fingerprints()
        with self.assertRaisesRegex(RuntimeError, "Injected migration failure"):
            apply_migrations(self.db, target_version=2, fail_after_statement=5, isolated=True)
        self.assertEqual(self.fingerprints(), source_before)
        state = inspect_database(self.db)
        self.assertEqual(state["foundation_001"], "APPLIED")
        self.assertEqual(state["price_evidence_002"], "ABSENT")
        self.assertEqual([row["version"] for row in state["migrations"]], [1])

        conn = self.connect()
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("price_evidence", names)
            self.assertNotIn("facebook_landing_page_fetches", names)
        finally:
            conn.close()

        retry = apply_migrations(self.db, target_version=2, isolated=True)
        self.assertEqual(self.fingerprints(), source_before)
        self.assertEqual(retry.states["002_price_evidence.sql"], "APPLIED")
        self.assertEqual(inspect_database(self.db)["price_evidence_002"], "APPLIED")

    def test_checksum_drift_is_rejected(self) -> None:
        apply_migrations(self.db, target_version=2, isolated=True)
        conn = self.connect()
        try:
            conn.execute("UPDATE schema_migrations SET checksum='drift' WHERE version=2")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(MigrationError, "checksum drift"):
            preflight_database(self.db, target_version=2)

    def test_structurally_incomplete_migration_registry_fails_preflight(self) -> None:
        conn = self.connect()
        try:
            conn.execute("DROP TABLE schema_migrations")
            conn.execute(
                "CREATE TABLE schema_migrations (version INTEGER, filename TEXT, checksum TEXT, "
                "applied_at TEXT, recorded_at TEXT, state TEXT, verification TEXT)"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(MigrationError, "unexpected structure or constraints"):
            preflight_database(self.db, target_version=2)

    def test_restore_verified_backup_recovers_from_partial_schema(self) -> None:
        source_before = self.fingerprints()
        backup = backup_database(self.db, migration_version=2, isolated=True)
        verify_backup(backup, expected_fingerprints=source_before)
        conn = self.connect()
        try:
            conn.execute("CREATE TABLE price_evidence (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(MigrationError, "partial or structurally incomplete"):
            preflight_database(self.db, target_version=2)
        conn = self.connect()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='price_evidence'").fetchone()[0],
                1,
            )
        finally:
            conn.close()

        restored = restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        self.assertEqual(restored["integrity_check"], "ok")
        self.assertEqual(restored["foreign_key_violations"], 0)
        self.assertEqual(self.fingerprints(), source_before)
        self.assertEqual(inspect_database(self.db)["price_evidence_002"], "ABSENT")

    def test_migration_002_rollback_is_refused_and_backup_restore_is_supported(self) -> None:
        run = apply_migrations(self.db, target_version=2, isolated=True)
        backup = Path(next(b["path"] for b in run.backups if "v002" in b["path"]))
        with self.assertRaisesRegex(MigrationError, "restore a verified pre-migration backup"):
            migrate.rollback(self.db, isolated=True)
        restore_verified_backup(self.db, backup, isolated=True, writers_stopped=True)
        state = inspect_database(self.db)
        self.assertEqual(state["foundation_001"], "APPLIED")
        self.assertEqual(state["price_evidence_002"], "ABSENT")

    def test_untracked_foundation_is_recorded_as_baseline_without_invented_apply_time(self) -> None:
        conn = self.connect()
        try:
            conn.execute("DROP TABLE schema_migrations")
            conn.commit()
        finally:
            conn.close()
        run = apply_migrations(self.db, target_version=1, isolated=True)
        self.assertIn("registry_baselines_recorded", run.validations[0])
        row = inspect_database(self.db)["migrations"][0]
        self.assertEqual(row["state"], "BASELINED")
        self.assertIsNone(row["applied_at"])
        self.assertEqual(row["checksum"], migration_checksum(1))

    def test_read_only_status_works_without_migration_002(self) -> None:
        before = self.db.read_bytes()
        state = preflight_database(self.db, target_version=1)
        self.assertEqual(state["foundation_001"], "APPLIED")
        self.assertEqual(state["price_evidence_002"], "ABSENT")
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
