from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
from price_evidence.repository import apply_price_evidence_migration
from sensors import ouedkniss_sensor as sensor


def make_v2_database(path: Path, *, with_price_evidence: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            canonical_name TEXT NOT NULL
        );
        CREATE TABLE sellers (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            source_seller_id TEXT,
            display_name TEXT,
            phone_number TEXT,
            wilaya TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (platform, source_seller_id)
        );
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            source_listing_id TEXT,
            seller_id INTEGER REFERENCES sellers(id) ON DELETE SET NULL,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            raw_title TEXT NOT NULL,
            url TEXT,
            status TEXT NOT NULL DEFAULT 'unknown'
                CHECK (status IN ('active', 'inactive', 'deleted', 'unknown')),
            first_seen_at TEXT,
            last_seen_at TEXT,
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (platform, source_listing_id)
        );
        CREATE UNIQUE INDEX ux_marketplace_listing_url
            ON marketplace_listings(platform, url) WHERE url IS NOT NULL;
        CREATE TABLE marketplace_listing_observations (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL,
            price REAL CHECK (price IS NULL OR price >= 0),
            currency TEXT NOT NULL DEFAULT 'DZD',
            status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'deleted', 'unknown')),
            availability TEXT,
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (listing_id, observed_at)
        );
        CREATE TRIGGER trg_listing_observations_no_update
        BEFORE UPDATE ON marketplace_listing_observations
        BEGIN
            SELECT RAISE(ABORT, 'marketplace observations are append-only');
        END;
        CREATE TRIGGER trg_listing_observations_no_delete
        BEFORE DELETE ON marketplace_listing_observations
        BEGIN
            SELECT RAISE(ABORT, 'marketplace observations are append-only');
        END;
        CREATE TABLE facebook_ads (id INTEGER PRIMARY KEY, library_id TEXT, url TEXT);
        CREATE TABLE facebook_ad_observations (
            id INTEGER PRIMARY KEY,
            ad_id INTEGER NOT NULL REFERENCES facebook_ads(id),
            observed_at TEXT NOT NULL,
            detected_metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()
    if with_price_evidence:
        apply_price_evidence_migration(conn)
    conn.close()


def api_response(
    *,
    seller_id: str = "seller-17",
    seller_name: str | None = "Seller Name",
    listing_id: str = "listing-25",
    title: str = "  Raw title, unchanged  ",
    price: object = ...,
) -> dict:
    announcement = {
        "id": listing_id,
        "title": title,
        "slug": "raw-listing-slug",
        "priceUnit": "UNIT",
        "priceType": "FIXED",
        "status": "PUBLISHED",
        "defaultMedia": {
            "mediaUrl": "https://cdn.example/image.jpg",
            "mimeType": "image/jpeg",
            "thumbnail": True,
            "__typename": "Media",
        },
        "__typename": "Announcement",
    }
    if price is not ...:
        announcement["price"] = price
    return {
        "data": {
            "storeList": [
                {
                    "target": {
                        "id": seller_id,
                        "name": seller_name,
                        "slug": "seller-slug",
                        "mainLocation": {
                            "location": {"region": {"name": "Alger"}}
                        },
                        "announcements": {"data": [announcement]},
                    }
                }
            ]
        }
    }


class OuedknissSensorV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "Database.sqlite"
        make_v2_database(self.db_path)
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.db_path

    def tearDown(self) -> None:
        config.DATABASE_PATH = self.original_database_path
        self.temp.cleanup()

    def open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def sensor_database(self):
        conn = self.open_db()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def capture(self, data: dict, observed_at: str, *, price_extractor=None):
        kwargs = {} if price_extractor is None else {"price_extractor": price_extractor}
        with patch.object(sensor, "connect", side_effect=self.open_db) as database_connect:
            result = sensor.process_and_store_data(data, observed_at, **kwargs)
        database_connect.assert_called_once_with()
        return result

    def seed_legacy_seller_listing(
        self,
        *,
        legacy_seller_id: int,
        listing_id: str,
        listing_source_id: str | None = None,
        listing_slug: str = "raw-listing-slug",
        display_name: str = "Seller Name",
    ) -> tuple[int, int]:
        with self.sensor_database() as conn:
            seller = conn.execute(
                """INSERT INTO sellers (
                       platform, source_seller_id, display_name, wilaya,
                       first_seen_at, last_seen_at, source_payload_json
                   ) VALUES ('Ouedkniss', NULL, ?, 'Legacy Wilaya', 'legacy-first',
                             'legacy-last', ?)""",
                (
                    display_name,
                    json.dumps(
                        {
                            "legacy_seller_id": legacy_seller_id,
                            "legacy_origin_platform": "Ouedkniss",
                        }
                    ),
                ),
            )
            seller_id = int(seller.lastrowid)
            listing_url = sensor._listing_url("seller-slug", listing_slug, listing_id)
            listing = conn.execute(
                """INSERT INTO marketplace_listings (
                       platform, source_listing_id, seller_id, raw_title, url, status,
                       first_seen_at, last_seen_at, source_payload_json
                   ) VALUES ('Ouedkniss', ?, ?, 'Migrated raw title', ?, 'unknown',
                             'legacy-first', 'legacy-last', '{}')""",
                (listing_source_id, seller_id, listing_url),
            )
            return seller_id, int(listing.lastrowid)

    def test_legacy_seller_is_adopted_from_matching_existing_listing(self) -> None:
        legacy_id, listing_id = self.seed_legacy_seller_listing(
            legacy_seller_id=41,
            listing_id="listing-25",
            listing_source_id="listing-25",
        )

        first = self.capture(api_response(), "2026-10-04T10:00:00Z")
        second = self.capture(api_response(), "2026-10-04T11:00:00Z")

        self.assertEqual(first.new_listings, 0)
        self.assertEqual(first.existing_listings, 1)
        self.assertEqual(first.observations, 1)
        self.assertEqual(second.observations, 1)
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listing_observations").fetchone()[0],
                2,
            )
            seller = conn.execute("SELECT * FROM sellers WHERE id = ?", (legacy_id,)).fetchone()
            self.assertEqual(seller["source_seller_id"], "seller-17")
            self.assertEqual(seller["display_name"], "Seller Name")
            self.assertEqual(seller["wilaya"], "Alger")
            self.assertEqual(seller["first_seen_at"], "legacy-first")
            self.assertEqual(seller["last_seen_at"], "2026-10-04T11:00:00.000000Z")
            seller_payload = json.loads(seller["source_payload_json"])
            self.assertEqual(seller_payload["legacy_seller_id"], 41)
            self.assertEqual(seller_payload["legacy_origin_platform"], "Ouedkniss")
            self.assertEqual(
                seller_payload["identity_resolution"]["method"], "legacy_listing_evidence"
            )
            self.assertEqual(
                seller_payload["identity_resolution"]["evidence"][0][
                    "marketplace_listing_id"
                ],
                listing_id,
            )
            listing = conn.execute(
                "SELECT seller_id FROM marketplace_listings WHERE id = ?", (listing_id,)
            ).fetchone()
            self.assertEqual(listing["seller_id"], legacy_id)

    def test_legacy_seller_can_be_adopted_by_canonical_url_when_listing_id_is_null(self) -> None:
        legacy_id, _listing_id = self.seed_legacy_seller_listing(
            legacy_seller_id=42,
            listing_id="listing-25",
            listing_source_id=None,
        )

        stats = self.capture(api_response(), "2026-10-04T10:00:00Z")

        self.assertEqual(stats.new_listings, 0)
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 1)
            seller = conn.execute("SELECT source_seller_id FROM sellers WHERE id = ?", (legacy_id,)).fetchone()
            listing = conn.execute("SELECT source_listing_id, seller_id FROM marketplace_listings").fetchone()
            self.assertEqual(seller["source_seller_id"], "seller-17")
            self.assertEqual(listing["source_listing_id"], "listing-25")
            self.assertEqual(listing["seller_id"], legacy_id)

    def test_ambiguous_listing_evidence_skips_store_without_adopting_either_seller(self) -> None:
        first_legacy_id, _ = self.seed_legacy_seller_listing(
            legacy_seller_id=51,
            listing_id="listing-25",
            listing_source_id="listing-25",
        )
        second_legacy_id, _ = self.seed_legacy_seller_listing(
            legacy_seller_id=52,
            listing_id="listing-26",
            listing_source_id="listing-26",
            listing_slug="second-listing-slug",
        )
        data = api_response()
        second_announcement = dict(data["data"]["storeList"][0]["target"]["announcements"]["data"][0])
        second_announcement.update({"id": "listing-26", "slug": "second-listing-slug"})
        data["data"]["storeList"][0]["target"]["announcements"]["data"].append(
            second_announcement
        )

        with self.assertLogs(sensor.logger, level="ERROR") as logs:
            stats = self.capture(data, "2026-10-04T10:00:00Z")

        self.assertEqual(stats.errors, 1)
        self.assertIn("seller identity conflict", "\n".join(logs.output).lower())
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 2)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listing_observations").fetchone()[0],
                0,
            )
            unresolved_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT source_seller_id FROM sellers WHERE id IN (?, ?) ORDER BY id",
                    (first_legacy_id, second_legacy_id),
                ).fetchall()
            ]
            self.assertEqual(unresolved_ids, [None, None])

    def test_display_name_similarity_alone_does_not_adopt_legacy_seller(self) -> None:
        legacy_id, _ = self.seed_legacy_seller_listing(
            legacy_seller_id=61,
            listing_id="unrelated-listing",
            listing_source_id="unrelated-listing",
            display_name="Seller Name",
        )

        self.capture(api_response(), "2026-10-04T10:00:00Z")

        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 2)
            self.assertIsNone(
                conn.execute("SELECT source_seller_id FROM sellers WHERE id = ?", (legacy_id,)).fetchone()[0]
            )
            self.assertEqual(
                conn.execute(
                    "SELECT source_seller_id FROM sellers WHERE source_seller_id = 'seller-17'"
                ).fetchone()[0],
                "seller-17",
            )

    def test_future_capture_resolves_directly_by_stable_source_seller_id(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        changed = api_response(listing_id="listing-26")

        stats = self.capture(changed, "2026-10-04T11:00:00Z")

        self.assertEqual(stats.new_listings, 1)
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 1)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(DISTINCT seller_id) FROM marketplace_listings"
                ).fetchone()[0],
                1,
            )

    def test_creates_seller_by_source_id_and_keeps_raw_source_fields(self) -> None:
        stats = self.capture(api_response(), "2026-10-04T10:00:00Z")
        self.assertEqual(stats.sellers_observed, 1)
        self.assertEqual(stats.new_listings, 1)

        with self.sensor_database() as conn:
            seller = conn.execute("SELECT * FROM sellers").fetchone()
            listing = conn.execute("SELECT * FROM marketplace_listings").fetchone()
            self.assertEqual(seller["platform"], "Ouedkniss")
            self.assertEqual(seller["source_seller_id"], "seller-17")
            self.assertEqual(seller["display_name"], "Seller Name")
            self.assertEqual(seller["wilaya"], "Alger")
            self.assertIsNotNone(seller["last_seen_at"])
            self.assertEqual(listing["source_listing_id"], "listing-25")
            self.assertEqual(listing["raw_title"], "  Raw title, unchanged  ")
            self.assertEqual(
                listing["url"],
                "https://www.ouedkniss.com/store/seller-slug/annonce/raw-listing-slug/listing-25",
            )
            self.assertEqual(listing["status"], "active")
            self.assertEqual(listing["seller_id"], seller["id"])

    def test_existing_seller_is_reused_by_source_id_not_display_name(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        self.capture(
            api_response(seller_name="Renamed Seller"),
            "2026-10-04T11:00:00Z",
        )
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 1)
            seller = conn.execute("SELECT * FROM sellers").fetchone()
            self.assertEqual(seller["source_seller_id"], "seller-17")
            self.assertEqual(seller["display_name"], "Renamed Seller")
            self.assertEqual(seller["last_seen_at"], "2026-10-04T11:00:00.000000Z")

    def test_distinct_source_ids_are_not_merged_by_same_display_name(self) -> None:
        first = api_response(seller_id="seller-17", listing_id="listing-25")
        second = api_response(seller_id="seller-18", listing_id="listing-26")
        self.capture(first, "2026-10-04T10:00:00Z")
        self.capture(second, "2026-10-04T11:00:00Z")
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 2)

    def test_existing_listing_is_reused_and_product_remains_null(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        second = self.capture(api_response(title="New exact source title"), "2026-10-04T11:00:00Z")
        self.assertEqual(second.new_listings, 0)
        self.assertEqual(second.existing_listings, 1)
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 1)
            listing = conn.execute("SELECT * FROM marketplace_listings").fetchone()
            self.assertEqual(listing["raw_title"], "New exact source title")
            self.assertIsNone(listing["product_id"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)

    def test_first_and_second_runs_append_observations_and_preserve_history(self) -> None:
        first_stats = self.capture(api_response(), "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            first = dict(conn.execute("SELECT * FROM marketplace_listing_observations").fetchone())
        second_stats = self.capture(api_response(), "2026-10-04T11:00:00Z")
        self.assertEqual(first_stats.observations, 1)
        self.assertEqual(second_stats.observations, 1)

        with self.sensor_database() as conn:
            rows = conn.execute(
                "SELECT * FROM marketplace_listing_observations ORDER BY observed_at"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(dict(rows[0]), first)
            self.assertEqual(rows[1]["status"], "active")
            self.assertEqual(rows[1]["availability"], "listed")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 1)

    def test_same_timestamp_does_not_duplicate_or_overwrite_observation(self) -> None:
        timestamp = "2026-10-04T10:00:00Z"
        self.capture(
            api_response(), timestamp, price_extractor=lambda _: (12500.0, "DZD")
        )
        with self.sensor_database() as conn:
            first = dict(conn.execute("SELECT * FROM marketplace_listing_observations").fetchone())
        stats = self.capture(
            api_response(), timestamp, price_extractor=lambda _: (100.0, "DZD")
        )
        self.assertEqual(stats.observations, 0)
        self.assertEqual(stats.duplicate_observations, 1)
        with self.sensor_database() as conn:
            row = dict(conn.execute("SELECT * FROM marketplace_listing_observations").fetchone())
            self.assertEqual(row, first)

    def test_unknown_price_is_null_and_currency_stays_unconfirmed(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            observation = conn.execute(
                "SELECT price, currency, source_payload_json FROM marketplace_listing_observations"
            ).fetchone()
            self.assertIsNone(observation["price"])
            self.assertNotEqual(observation["price"], 0)
            self.assertEqual(observation["currency"], "UNKNOWN")
            payload = json.loads(observation["source_payload_json"])
            self.assertEqual(
                payload["observation"]["currency_basis"],
                "currency_unconfirmed",
            )

    def test_source_numeric_price_is_not_put_in_legacy_money_column_without_currency(self) -> None:
        stats = self.capture(
            api_response(price=14500),
            "2026-10-04T10:00:00Z",
        )
        self.assertEqual(stats.observations_with_null_price, 1)
        with self.sensor_database() as conn:
            observation = conn.execute(
                "SELECT price, currency, source_payload_json FROM marketplace_listing_observations"
            ).fetchone()
            self.assertIsNone(observation["price"])
            self.assertEqual(observation["currency"], "UNKNOWN")
            self.assertEqual(json.loads(observation["source_payload_json"])['announcement']['price'], 14500)
            self.assertEqual(
                json.loads(observation["source_payload_json"])["observation"]["price_extraction"],
                "FrontStoreQuery.price",
            )

    def test_declared_million_amount_preserves_source_value_without_assuming_currency(self) -> None:
        data = api_response(price=1)
        data["data"]["storeList"][0]["target"]["announcements"]["data"][0][
            "priceUnit"
        ] = "MILLION"
        self.capture(data, "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            observation = conn.execute(
                "SELECT price, currency, source_payload_json FROM marketplace_listing_observations"
            ).fetchone()
            self.assertIsNone(observation["price"])
            self.assertEqual(observation["currency"], "UNKNOWN")
            payload = json.loads(observation["source_payload_json"])
            self.assertEqual(payload["announcement"]["price"], 1)
            self.assertEqual(payload["announcement"]["price_unit"], "MILLION")

    def test_capture_appends_candidate_to_evidence_layer_when_migration_is_present(self) -> None:
        with self.sensor_database() as conn:
            apply_price_evidence_migration(conn)

        self.capture(api_response(price=1), "2026-10-04T10:00:00Z")

        with self.sensor_database() as conn:
            observation = conn.execute(
                "SELECT price, currency, source_payload_json FROM marketplace_listing_observations"
            ).fetchone()
            self.assertIsNone(observation["price"])
            self.assertEqual(observation["currency"], "UNKNOWN")
            raw = json.loads(observation["source_payload_json"])
            self.assertEqual(raw["announcement"]["price"], 1)
            self.assertEqual(raw["announcement"]["price_unit"], "UNIT")
            evidence = conn.execute("SELECT * FROM price_evidence").fetchone()
            self.assertEqual(evidence["candidate_amount"], 1.0)
            self.assertEqual(evidence["currency_code"], None)
            self.assertEqual(evidence["currency_basis"], "UNKNOWN")
            self.assertEqual(evidence["unit_status"], "UNRESOLVED")
            self.assertEqual(evidence["quality_status"], "UNIT_OR_CURRENCY_UNCONFIRMED")
            self.assertNotEqual(evidence["currency_code"], "DZD")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)

    def _remove_price_evidence_schema(self) -> None:
        with self.sensor_database() as conn:
            for name in (
                "trusted_price_evidence",
                "trg_price_evidence_platform_guard",
                "trg_landing_fetch_no_update",
                "trg_landing_fetch_no_delete",
                "trg_price_evidence_no_update",
                "trg_price_evidence_no_delete",
                "trg_price_evidence_reviews_no_update",
                "trg_price_evidence_reviews_no_delete",
                "ix_landing_page_fetch_observation_time",
                "ix_price_evidence_marketplace_observation",
                "ix_price_evidence_landing_fetch",
                "ix_price_evidence_source_time",
                "ix_price_evidence_reviews_latest",
            ):
                conn.execute(f'DROP {"VIEW" if name == "trusted_price_evidence" else "TRIGGER" if name.startswith("trg_") else "INDEX"} IF EXISTS "{name}"')
            for table in ("price_evidence_reviews", "price_evidence", "facebook_landing_page_fetches"):
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    def test_missing_schema_stops_run_sensor_before_fetch_or_database_writes(self) -> None:
        self._remove_price_evidence_schema()
        with patch.object(sensor, "fetch_data") as fetch, patch.object(sensor, "connect") as writer:
            with self.assertRaises(sensor.PriceEvidenceSchemaNotReady) as raised:
                sensor.run_sensor()
        fetch.assert_not_called()
        writer.assert_not_called()
        self.assertIn("Migration 002", str(raised.exception))
        self.assertIn(str(self.db_path.resolve()), str(raised.exception))

    def test_missing_schema_stops_direct_capture_before_any_source_writes(self) -> None:
        self._remove_price_evidence_schema()
        with self.sensor_database() as conn:
            before = tuple(
                conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in ("sellers", "marketplace_listings", "marketplace_listing_observations")
            )
        with self.assertRaises(sensor.PriceEvidenceSchemaNotReady):
            sensor.process_and_store_data(api_response(price=1), "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            after = tuple(
                conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in ("sellers", "marketplace_listings", "marketplace_listing_observations")
            )
        self.assertEqual(after, before)
        conn = self.open_db()
        try:
            self.assertNotIn("price_evidence", {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master")
            })
        finally:
            conn.close()

    def test_structurally_incomplete_schema_fails_closed(self) -> None:
        with self.sensor_database() as conn:
            conn.execute("DROP TRIGGER trg_price_evidence_no_update")
        with patch.object(sensor, "fetch_data") as fetch:
            with self.assertRaises(sensor.PriceEvidenceSchemaNotReady):
                sensor.run_sensor()
        fetch.assert_not_called()

    def test_valid_schema_allows_existing_synthetic_capture_path(self) -> None:
        with patch.object(sensor, "fetch_data", return_value=api_response(price=1)) as fetch, \
                patch.object(sensor, "polite_sleep"), \
                patch.object(sensor, "connect", side_effect=self.open_db):
            stats = sensor.run_sensor()
        fetch.assert_called_once_with()
        self.assertEqual(stats.observations, 1)
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_evidence").fetchone()[0], 1)

    def test_unrecognized_source_status_is_not_marked_inactive_or_deleted(self) -> None:
        data = api_response()
        data["data"]["storeList"][0]["target"]["announcements"]["data"][0][
            "status"
        ] = "ARCHIVED"
        self.capture(data, "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            listing_status = conn.execute(
                "SELECT status FROM marketplace_listings"
            ).fetchone()[0]
            observation_status = conn.execute(
                "SELECT status FROM marketplace_listing_observations"
            ).fetchone()[0]
            self.assertEqual(listing_status, "unknown")
            self.assertEqual(observation_status, "unknown")

    def test_missing_optional_slugs_do_not_create_fake_url_or_erase_known_url(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        changed = api_response()
        store = changed["data"]["storeList"][0]["target"]
        store["slug"] = None
        store["announcements"]["data"][0]["slug"] = None
        self.capture(changed, "2026-10-04T11:00:00Z")
        with self.sensor_database() as conn:
            listing = conn.execute("SELECT url FROM marketplace_listings").fetchone()
            self.assertEqual(
                listing["url"],
                "https://www.ouedkniss.com/store/seller-slug/annonce/raw-listing-slug/listing-25",
            )

    def test_missing_stable_listing_identity_is_logged_and_skipped(self) -> None:
        data = api_response()
        data["data"]["storeList"][0]["target"]["announcements"]["data"][0]["id"] = None
        with self.assertLogs(sensor.logger, level="ERROR") as logs:
            stats = self.capture(data, "2026-10-04T10:00:00Z")
        self.assertEqual(stats.errors, 1)
        self.assertIn("Missing stable listing ID", "\n".join(logs.output))
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 0)

    def test_missing_stable_seller_identity_is_logged_and_skipped(self) -> None:
        data = api_response()
        data["data"]["storeList"][0]["target"]["id"] = None
        with self.assertLogs(sensor.logger, level="ERROR") as logs:
            stats = self.capture(data, "2026-10-04T10:00:00Z")
        self.assertEqual(stats.errors, 1)
        self.assertIn("Missing stable seller ID", "\n".join(logs.output))
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 0)

    def test_price_extraction_error_is_logged_and_observation_price_is_null(self) -> None:
        def broken_extractor(_announcement):
            raise ValueError("detail response did not contain a verified price")

        with self.assertLogs(sensor.logger, level="ERROR") as logs:
            stats = self.capture(
                api_response(),
                "2026-10-04T10:00:00Z",
                price_extractor=broken_extractor,
            )
        self.assertEqual(stats.errors, 1)
        self.assertIn("Price extraction failed", "\n".join(logs.output))
        with self.sensor_database() as conn:
            price = conn.execute(
                "SELECT price FROM marketplace_listing_observations"
            ).fetchone()[0]
            self.assertIsNone(price)

    def test_timestamps_must_have_timezone_semantics(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            sensor.utc_timestamp("2026-10-04T10:00:00")
        self.assertEqual(
            sensor.utc_timestamp("2026-10-04T13:00:00+03:00"),
            "2026-10-04T10:00:00.000000Z",
        )

    def test_transaction_rolls_back_seller_and_listing_if_observation_fails(self) -> None:
        with self.sensor_database() as conn:
            conn.execute(
                """CREATE TRIGGER fail_observation BEFORE INSERT ON marketplace_listing_observations
                   BEGIN SELECT RAISE(ABORT, 'forced observation failure'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.capture(api_response(), "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listing_observations").fetchone()[0],
                0,
            )

    def test_transaction_rolls_back_legacy_seller_adoption_if_observation_fails(self) -> None:
        legacy_id, listing_id = self.seed_legacy_seller_listing(
            legacy_seller_id=71,
            listing_id="listing-25",
            listing_source_id="listing-25",
        )
        with self.sensor_database() as conn:
            conn.execute(
                """CREATE TRIGGER fail_observation BEFORE INSERT ON marketplace_listing_observations
                   BEGIN SELECT RAISE(ABORT, 'forced observation failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.capture(api_response(), "2026-10-04T10:00:00Z")

        with self.sensor_database() as conn:
            seller = conn.execute("SELECT * FROM sellers WHERE id = ?", (legacy_id,)).fetchone()
            listing = conn.execute(
                "SELECT seller_id, source_listing_id FROM marketplace_listings WHERE id = ?",
                (listing_id,),
            ).fetchone()
            self.assertIsNone(seller["source_seller_id"])
            self.assertEqual(seller["first_seen_at"], "legacy-first")
            self.assertEqual(seller["last_seen_at"], "legacy-last")
            self.assertEqual(
                json.loads(seller["source_payload_json"]),
                {"legacy_seller_id": 71, "legacy_origin_platform": "Ouedkniss"},
            )
            self.assertEqual(listing["seller_id"], legacy_id)
            self.assertEqual(listing["source_listing_id"], "listing-25")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM marketplace_listing_observations").fetchone()[0],
                0,
            )

    def test_connection_comes_from_config_and_old_database_path_is_absent(self) -> None:
        self.assertIs(sensor.connect, config.connect)
        source = Path(sensor.__file__).read_text(encoding="utf-8")
        self.assertIn("from config import connect", source)
        self.assertNotIn(r"C:\Users\Idris\DataGripProjects\default\Database.sqlite", source)

    def test_foreign_key_integrity_is_valid_after_capture(self) -> None:
        self.capture(api_response(), "2026-10-04T10:00:00Z")
        with self.sensor_database() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                conn.execute("SELECT product_id FROM marketplace_listings").fetchone()[0],
                None,
            )


if __name__ == "__main__":
    unittest.main()
