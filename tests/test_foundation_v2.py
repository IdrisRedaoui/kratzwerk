from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import migrate
from migration_lifecycle import MigrationError, apply_migrations, inspect_database
from schema_validation import _canonical_sql


def make_legacy_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE Products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unified_name TEXT NOT NULL, category TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE Sellers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_name TEXT NOT NULL, origin_platform TEXT NOT NULL,
            phone_number TEXT, wilaya TEXT NOT NULL
        );
        CREATE TABLE Marketplace_Listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL, raw_title TEXT NOT NULL, current_price REAL,
            platform TEXT NOT NULL, listing_url TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'active', last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(product_id) REFERENCES Products(id) ON DELETE CASCADE,
            FOREIGN KEY(seller_id) REFERENCES Sellers(id) ON DELETE CASCADE
        );
        CREATE TABLE Price_History (
            id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL,
            recorded_price REAL NOT NULL, recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(listing_id) REFERENCES Marketplace_Listings(id) ON DELETE CASCADE
        );
        CREATE TABLE Advertising_Campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            ad_url TEXT UNIQUE NOT NULL, platform TEXT NOT NULL, start_date DATETIME NOT NULL,
            status TEXT DEFAULT 'active', targeted_wilayas TEXT,
            FOREIGN KEY(product_id) REFERENCES Products(id) ON DELETE CASCADE
        );
        INSERT INTO Products(id, unified_name, category) VALUES
            (1, 'Legacy title product', 'phones');
        INSERT INTO Sellers(id, seller_name, origin_platform, wilaya)
            VALUES (1, 'seller', 'Ouedkniss', 'Alger');
        INSERT INTO Marketplace_Listings(id, product_id, seller_id, raw_title, current_price,
             platform, listing_url, status, last_seen)
            VALUES (10, 1, 1, 'Raw phone title', 0, 'Ouedkniss',
                    'https://example.test/listing/10', 'active', '2026-01-02 10:00:00');
        INSERT INTO Advertising_Campaigns(id, product_id, ad_url, platform, start_date,
             status, targeted_wilayas)
            VALUES (20, 1, 'https://example.test/ad/20', 'facebook', '2025-12-01',
                    'active', 'Alger');
    """)
    conn.commit()
    conn.close()


class FoundationMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "Database.sqlite"
        make_legacy_database(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def test_migration_preserves_legacy_and_keeps_raw_entities_unmapped(self) -> None:
        backup = migrate.apply(self.db, isolated=True)
        self.assertTrue(backup.is_file())
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM legacy_products").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM legacy_marketplace_listings").fetchone()[0], 1)
            listing = conn.execute(
                "SELECT product_id, raw_title FROM marketplace_listings WHERE id=10"
            ).fetchone()
            self.assertEqual(listing, (None, "Raw phone title"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads WHERE product_id IS NULL").fetchone()[0], 1)
            observation = conn.execute(
                "SELECT price, observed_at FROM marketplace_listing_observations WHERE listing_id=10"
            ).fetchone()
            self.assertEqual(observation[0], None)  # legacy zero was not treated as a real price
            self.assertEqual(observation[1], "2026-01-02 10:00:00")

    def test_history_deduplication_and_cross_source_product_link(self) -> None:
        migrate.apply(self.db, isolated=True)
        with self.connect() as conn:
            product_id = conn.execute(
                "INSERT INTO products(canonical_name) VALUES ('Normalized phone')"
            ).lastrowid
            conn.execute("UPDATE marketplace_listings SET product_id=? WHERE id=10", (product_id,))
            conn.execute("UPDATE facebook_ads SET product_id=? WHERE id=20", (product_id,))
            conn.execute("""INSERT INTO marketplace_listing_observations
                (listing_id, observed_at, price, status) VALUES (10, '2026-01-03', 18000, 'active')""")
            conn.execute("""INSERT INTO marketplace_listing_observations
                (listing_id, observed_at, price, status) VALUES (10, '2026-01-04', 17500, 'active')""")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM marketplace_listing_observations WHERE listing_id=10"
            ).fetchone()[0], 3)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO marketplace_listing_observations
                    (listing_id, observed_at, price, status) VALUES (10, '2026-01-04', 17000, 'active')""")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE marketplace_listing_observations SET price=17000 WHERE listing_id=10 AND observed_at='2026-01-04'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM marketplace_listing_observations WHERE listing_id=10 AND observed_at='2026-01-04'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE facebook_ad_observations SET active=0 WHERE ad_id=20")
            conn.execute("""INSERT INTO product_mappings
                (source_type, source_id, product_id, confidence, mapping_source)
                VALUES ('marketplace_listing', 10, ?, 0.95, 'test')""", (product_id,))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_foreign_key_and_url_uniqueness_are_enforced(self) -> None:
        migrate.apply(self.db, isolated=True)
        with self.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO marketplace_listings(platform, raw_title, seller_id)
                    VALUES ('Ouedkniss', 'orphan', 999)""")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO facebook_ads(url, first_seen_at, last_seen_at)
                    VALUES ('https://example.test/ad/20', '2026-01-01', '2026-01-01')""")

    def test_foundation_rollback_is_refused_without_creating_backup_or_dropping_objects(self) -> None:
        migrate.apply(self.db, isolated=True)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE application_extension (id INTEGER PRIMARY KEY, value TEXT)")
            conn.commit()
        finally:
            conn.close()
        before = self.db.read_bytes()
        backup_paths = set(self.db.parent.glob(self.db.name + ".pre-migration-*.bak"))
        with self.assertRaisesRegex(MigrationError, "rollback is disabled"):
            migrate.rollback(self.db, isolated=True)
        conn = sqlite3.connect(self.db)
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("legacy_products", names)
            self.assertIn("legacy_marketplace_listings", names)
            self.assertIn("marketplace_listings", names)
            self.assertIn("application_extension", names)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM legacy_marketplace_listings").fetchone()[0], 1
            )
        finally:
            conn.close()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(set(self.db.parent.glob(self.db.name + ".pre-migration-*.bak")), backup_paths)


class FoundationSchemaSafetyTests(unittest.TestCase):
    def test_sql_canonicalization_normalizes_formatting_but_preserves_sql_semantics(self) -> None:
        self.assertEqual(
            _canonical_sql('CREATE TABLE "Foo" (value TEXT) -- harmless comment\n'),
            _canonical_sql(' create /* harmless */ table [foo] ( value text ) '),
        )
        self.assertNotEqual(
            _canonical_sql("CHECK (status = 'deleted')"),
            _canonical_sql("CHECK (status = 'DELETED')"),
        )
        self.assertNotEqual(_canonical_sql("SELECT TRUE"), _canonical_sql('SELECT "true"'))
        self.assertNotEqual(
            _canonical_sql('CREATE TABLE "Straße" (value TEXT)'),
            _canonical_sql('CREATE TABLE "STRASSE" (value TEXT)'),
        )
        self.assertEqual(
            _canonical_sql('CREATE TABLE products (canonical_name TEXT)'),
            _canonical_sql('CREATE TABLE "PRODUCTS" ([CANONICAL_NAME] text)'),
        )

    def _prepare_untracked_foundation(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        db = Path(temp.name) / "foundation.sqlite"
        make_legacy_database(db)
        migrate.apply(db, isolated=True)
        conn = sqlite3.connect(db)
        try:
            conn.execute("DROP TABLE schema_migrations")
            conn.commit()
        finally:
            conn.close()
        for backup in db.parent.glob(db.name + ".pre-migration-*.bak"):
            backup.unlink()
        return temp, db

    @staticmethod
    def _rebuild_table(
        conn: sqlite3.Connection,
        table: str,
        create_sql: str,
        columns: str,
        indexes: tuple[str, ...] = (),
    ) -> None:
        rebuilt = f"{table}_rebuilt"
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(create_sql.replace("{rebuilt}", rebuilt))
        conn.execute(
            f'INSERT INTO "{rebuilt}" ({columns}) SELECT {columns} FROM "{table}"'
        )
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{rebuilt}" RENAME TO "{table}"')
        for statement in indexes:
            conn.execute(statement)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

    def _assert_rejected_without_baseline_or_backup(self, mutate) -> None:
        temp, db = self._prepare_untracked_foundation()
        try:
            conn = sqlite3.connect(db)
            try:
                mutate(conn)
                conn.commit()
            finally:
                conn.close()

            before = db.read_bytes()
            backups_before = set(db.parent.glob(db.name + ".pre-migration-*.bak"))
            self.assertEqual(inspect_database(db)["foundation_001"], "PARTIAL")
            with self.assertRaisesRegex(MigrationError, "partial or structurally incomplete"):
                apply_migrations(db, target_version=1, isolated=True)

            conn = sqlite3.connect(db)
            try:
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone())
            finally:
                conn.close()
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(set(db.parent.glob(db.name + ".pre-migration-*.bak")), backups_before)
        finally:
            temp.cleanup()

    def test_ascii_identifier_case_and_quoting_equivalence_is_accepted(self) -> None:
        temp, db = self._prepare_untracked_foundation()
        try:
            conn = sqlite3.connect(db)
            try:
                definition = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='products'"
                ).fetchone()[0]
                equivalent = definition.replace(
                    "CREATE TABLE products", 'CREATE TABLE "PRODUCTS"'
                ).replace("canonical_name", '"CANONICAL_NAME"')
                conn.execute("PRAGMA writable_schema=ON")
                conn.execute(
                    "UPDATE sqlite_master SET name='PRODUCTS', sql=? WHERE type='table' AND name='products'",
                    (equivalent,),
                )
                version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
                conn.execute(f"PRAGMA schema_version={version + 1}")
                conn.execute("PRAGMA writable_schema=OFF")
            finally:
                conn.close()
            self.assertEqual(inspect_database(db)["foundation_001"], "APPLIED")
            apply_migrations(db, target_version=1, isolated=True)
        finally:
            temp.cleanup()

    def test_meaningful_identifier_drift_is_still_rejected(self) -> None:
        def rename_column(conn: sqlite3.Connection) -> None:
            definition = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='products'"
            ).fetchone()[0]
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='products'",
                (definition.replace("canonical_name", "canonical_label"),),
            )
            version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
            conn.execute(f"PRAGMA schema_version={version + 1}")
            conn.execute("PRAGMA writable_schema=OFF")

        self._assert_rejected_without_baseline_or_backup(rename_column)

    def test_each_missing_or_drifted_foundation_definition_fails_closed(self) -> None:
        mutations = {
            "missing Facebook URL index": lambda conn: conn.execute(
                "DROP INDEX ux_facebook_ads_url"
            ),
            "missing required foreign key": lambda conn: self._rebuild_table(
                conn,
                "marketplace_listings",
                """CREATE TABLE {rebuilt} (
                    id INTEGER PRIMARY KEY, platform TEXT NOT NULL, source_listing_id TEXT,
                    seller_id INTEGER, product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    raw_title TEXT NOT NULL, url TEXT,
                    status TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (status IN ('active', 'inactive', 'deleted', 'unknown')),
                    first_seen_at TEXT, last_seen_at TEXT,
                    source_payload_json TEXT NOT NULL DEFAULT '{}',
                    CHECK (json_valid(source_payload_json)), UNIQUE (platform, source_listing_id)
                )""",
                "id, platform, source_listing_id, seller_id, product_id, raw_title, url, status, first_seen_at, last_seen_at, source_payload_json",
                (
                    "CREATE UNIQUE INDEX ux_marketplace_listing_url ON marketplace_listings(platform, url) WHERE url IS NOT NULL",
                    "CREATE INDEX ix_marketplace_listings_seller ON marketplace_listings(seller_id)",
                    "CREATE INDEX ix_marketplace_listings_product ON marketplace_listings(product_id)",
                    "CREATE INDEX ix_marketplace_listings_status ON marketplace_listings(status)",
                ),
            ),
            "missing required unique constraint": lambda conn: self._rebuild_table(
                conn,
                "sellers",
                """CREATE TABLE {rebuilt} (
                    id INTEGER PRIMARY KEY, platform TEXT NOT NULL, source_seller_id TEXT,
                    display_name TEXT, phone_number TEXT, wilaya TEXT, first_seen_at TEXT,
                    last_seen_at TEXT, source_payload_json TEXT NOT NULL DEFAULT '{}',
                    CHECK (json_valid(source_payload_json))
                )""",
                "id, platform, source_seller_id, display_name, phone_number, wilaya, first_seen_at, last_seen_at, source_payload_json",
            ),
            "missing required check constraint": lambda conn: self._rebuild_table(
                conn,
                "products",
                """CREATE TABLE {rebuilt} (
                    id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, category TEXT,
                    attributes_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""",
                "id, canonical_name, category, attributes_json, created_at, updated_at",
            ),
            "altered migration-defined table": lambda conn: conn.execute(
                "ALTER TABLE products ADD COLUMN unexpected TEXT"
            ),
            "altered migration-defined index": lambda conn: (
                conn.execute("DROP INDEX ux_facebook_ads_url"),
                conn.execute(
                    "CREATE UNIQUE INDEX ux_facebook_ads_url ON facebook_ads(platform) WHERE url IS NOT NULL"
                ),
            ),
            "missing required trigger": lambda conn: conn.execute(
                "DROP TRIGGER trg_ad_observations_no_delete"
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(finding=label):
                self._assert_rejected_without_baseline_or_backup(mutation)

    def test_check_literal_case_drift_is_rejected_before_baselining(self) -> None:
        def change_literal_only(conn: sqlite3.Connection) -> None:
            definition = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='marketplace_listings'"
            ).fetchone()[0]
            self.assertIn("'deleted'", definition)
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='marketplace_listings'",
                (definition.replace("'deleted'", "'DELETED'"),),
            )
            version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
            conn.execute(f"PRAGMA schema_version={version + 1}")
            conn.execute("PRAGMA writable_schema=OFF")

        self._assert_rejected_without_baseline_or_backup(change_literal_only)

    def test_unexpected_insert_blocking_trigger_is_rejected_before_baselining(self) -> None:
        def add_blocker(conn: sqlite3.Connection) -> None:
            conn.execute("""
                CREATE TRIGGER unexpected_blocker
                BEFORE INSERT ON marketplace_listings
                BEGIN
                    SELECT RAISE(ABORT, 'unexpected Foundation blocker');
                END
            """)

        self._assert_rejected_without_baseline_or_backup(add_blocker)


if __name__ == "__main__":
    unittest.main()
