from __future__ import annotations

import json
import hashlib
import os
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from price_evidence.facebook import LandingPageExtraction, extract_landing_page_prices
from price_evidence.models import PriceCandidate
from price_evidence.ouedkniss import assess_ouedkniss_price
from price_evidence.repository import (
    append_facebook_price_evidence,
    append_landing_page_fetch,
    append_ouedkniss_price_evidence,
    append_price_evidence_review,
    apply_price_evidence_migration,
)
from price_evidence.recovery import recover_ouedkniss_price_evidence


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "price_evidence"
def assert_approval_unavailable(testcase: unittest.TestCase, conn: sqlite3.Connection,
                                evidence_id: int, **kwargs) -> None:
    kwargs["decision"] = "APPROVED"
    with testcase.assertRaisesRegex(ValueError, "APPROVED is unavailable"):
        append_price_evidence_review(conn, evidence_id, **kwargs)


def independent_currency_evidence(code: str, label: str = "DZD") -> dict:
    content = f"Independent fixture price is denominated in {label}."
    return {
        "currency_evidence": [{
            "source_url": "https://independent.example/price-policy",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "currency_code": code,
        }]
    }


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY, platform TEXT NOT NULL,
            source_listing_id TEXT, url TEXT, raw_title TEXT NOT NULL
        );
        CREATE TABLE marketplace_listing_observations (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL, price REAL, currency TEXT NOT NULL DEFAULT 'DZD',
            status TEXT NOT NULL DEFAULT 'unknown', availability TEXT,
            source_payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE facebook_ads (
            id INTEGER PRIMARY KEY, library_id TEXT, url TEXT
        );
        CREATE TABLE facebook_ad_observations (
            id INTEGER PRIMARY KEY,
            ad_id INTEGER NOT NULL REFERENCES facebook_ads(id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL, detected_metadata_json TEXT NOT NULL DEFAULT '{}'
        );
    """)
    apply_price_evidence_migration(conn)
    return conn


def seed_ouedkniss(conn: sqlite3.Connection, listing_id: int, observation_id: int,
                   observed_at: str, *, amount=1, unit="UNIT", price_type="OFFERED") -> None:
    conn.execute(
        "INSERT INTO marketplace_listings(id,platform,source_listing_id,url,raw_title) VALUES(?,?,?,?,?)",
        (listing_id, "Ouedkniss", f"listing-{listing_id}", f"https://example.test/{listing_id}", "Raw title"),
    )
    payload = {"source": "Ouedkniss", "announcement": {
        "source_listing_id": f"listing-{listing_id}", "price": amount,
        "price_unit": unit, "price_type": price_type,
    }}
    conn.execute(
        """INSERT INTO marketplace_listing_observations
           (id,listing_id,observed_at,price,currency,status,source_payload_json)
           VALUES(?,?,?,1,'DZD','active',?)""",
        (observation_id, listing_id, observed_at, json.dumps(payload)),
    )


def seed_facebook(conn: sqlite3.Connection, *, ad_id=20, observation_id=200,
                  observed_at="2026-10-01T10:00:00Z", url="https://shop.example/item") -> None:
    conn.execute(
        "INSERT INTO facebook_ads(id,library_id,url) VALUES(?,?,?)",
        (ad_id, f"library-{ad_id}", f"https://facebook.test/ads/{ad_id}"),
    )
    conn.execute(
        """INSERT INTO facebook_ad_observations
           (id,ad_id,observed_at,detected_metadata_json) VALUES(?,?,?,?)""",
        (observation_id, ad_id, observed_at, json.dumps({"landing_url": url})),
    )


class OuedknissPriceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.conn = make_database(Path(self.temp.name) / "price-evidence.sqlite")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def test_one_unit_preserves_candidate_but_currency_and_unit_stay_unconfirmed(self) -> None:
        result = assess_ouedkniss_price({"price": 1, "priceUnit": "UNIT", "priceType": "OFFERED"})
        self.assertEqual(result.candidate_amount, 1.0)
        self.assertIsNone(result.currency_code)
        self.assertEqual(result.currency_basis, "UNKNOWN")
        self.assertEqual(result.unit_status, "UNRESOLVED")
        self.assertEqual(result.quality_status, "UNIT_OR_CURRENCY_UNCONFIRMED")
        self.assertIn("SOURCE_UNIT_UNIT_UNRESOLVED", result.reason_codes)
        self.assertIn("CURRENCY_NOT_ESTABLISHED_BY_SOURCE", result.reason_codes)

    def test_repeated_one_unit_pattern_is_flagged_without_claiming_false(self) -> None:
        observed_at = "2026-10-10T10:00:00Z"
        for index in range(1, 4):
            seed_ouedkniss(self.conn, index, index, observed_at)
        ids = [append_ouedkniss_price_evidence(self.conn, i, extracted_at=observed_at) for i in range(1, 4)]
        rows = self.conn.execute(
            "SELECT quality_status,reason_codes_json,candidate_amount,currency_code FROM price_evidence ORDER BY id"
        ).fetchall()
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(row["quality_status"] == "SUSPICIOUS_PLACEHOLDER" for row in rows))
        self.assertTrue(all("REPEATED_ONE_UNIT_PATTERN_REVIEW" in row["reason_codes_json"] for row in rows))
        self.assertTrue(all(row["candidate_amount"] == 1.0 and row["currency_code"] is None for row in rows))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)

    def test_reprocessing_is_idempotent_and_preserves_original_observation(self) -> None:
        seed_ouedkniss(self.conn, 1, 1, "2026-10-10T10:00:00Z")
        before = tuple(self.conn.execute("SELECT price,currency,source_payload_json FROM marketplace_listing_observations").fetchone())
        one = append_ouedkniss_price_evidence(self.conn, 1, extracted_at="2026-10-10T11:00:00Z")
        two = append_ouedkniss_price_evidence(self.conn, 1, extracted_at="2026-10-10T12:00:00Z")
        self.assertEqual(one, two)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM price_evidence").fetchone()[0], 1)
        after = tuple(self.conn.execute("SELECT price,currency,source_payload_json FROM marketplace_listing_observations").fetchone())
        self.assertEqual(before, after)

    def test_historical_recovery_preserves_exact_provenance_and_is_resumable(self) -> None:
        observed_at = "2026-10-10T10:00:00Z"
        for index in range(1, 4):
            seed_ouedkniss(self.conn, index, index, observed_at, amount=1, unit="UNIT")
        seed_ouedkniss(
            self.conn, 4, 4, "2026-10-11T10:00:00Z", amount=None, unit="UNIT", price_type="FIXED"
        )
        self.conn.execute(
            "UPDATE marketplace_listings SET source_listing_id='mutable-current-id', "
            "url='https://mutable.example/current' WHERE id=1"
        )
        source_before = [tuple(row) for row in self.conn.execute(
            "SELECT id,listing_id,observed_at,price,currency,source_payload_json "
            "FROM marketplace_listing_observations ORDER BY id"
        )]
        self.conn.commit()
        self.conn.isolation_level = None

        first = recover_ouedkniss_price_evidence(
            self.conn, extracted_at="2026-10-12T00:00:00Z"
        )
        self.assertEqual(first["source_observations"], 4)
        self.assertEqual(first["imported"], 4)
        self.assertEqual(first["ambiguous"], 3)
        self.assertEqual(first["missing"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertTrue(first["source_observations_unchanged"])
        self.assertEqual(first["suspicious_one_unit_rows"], 3)

        rows = self.conn.execute(
            """SELECT e.*, o.observed_at, o.source_payload_json
               FROM price_evidence e
               JOIN marketplace_listing_observations o ON o.id=e.marketplace_observation_id
               ORDER BY o.id"""
        ).fetchall()
        self.assertEqual(len(rows), 4)
        for row in rows[:3]:
            self.assertEqual(row["source_observed_at"], observed_at)
            self.assertEqual(row["price_observed_at"], observed_at)
            self.assertEqual(row["candidate_amount"], 1.0)
            self.assertEqual(row["raw_value_json"], "1")
            self.assertEqual(row["source_unit"], "UNIT")
            self.assertIsNone(row["currency_code"])
            self.assertEqual(row["currency_basis"], "UNKNOWN")
            self.assertEqual(row["unit_status"], "UNRESOLVED")
            self.assertEqual(row["quality_status"], "SUSPICIOUS_PLACEHOLDER")
            evidence = json.loads(row["supporting_evidence_json"])
            self.assertEqual(evidence["source_price_type"], "OFFERED")
            payload = json.loads(row["source_payload_json"])
            self.assertEqual(payload["announcement"]["price"], 1)
        self.assertEqual(rows[0]["source_entity_key"], "listing-1")
        self.assertIsNone(rows[0]["original_url"])
        missing = rows[3]
        self.assertIsNone(missing["candidate_amount"])
        self.assertEqual(missing["raw_value_json"], "null")
        self.assertEqual(missing["extraction_status"], "MISSING")
        self.assertEqual(missing["quality_status"], "MISSING")
        self.assertIsNone(missing["currency_code"])

        source_after = [tuple(row) for row in self.conn.execute(
            "SELECT id,listing_id,observed_at,price,currency,source_payload_json "
            "FROM marketplace_listing_observations ORDER BY id"
        )]
        self.assertEqual(source_after, source_before)
        second = recover_ouedkniss_price_evidence(
            self.conn, extracted_at="2026-10-13T00:00:00Z"
        )
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["already_present"], 4)
        self.assertEqual(second["ambiguous_evidence_rows"], 3)
        self.assertEqual(second["missing_evidence_rows"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM price_evidence").fetchone()[0], 4)

    def test_new_source_observation_appends_new_evidence_without_overwrite(self) -> None:
        seed_ouedkniss(self.conn, 1, 1, "2026-10-10T10:00:00Z", amount=1)
        first = append_ouedkniss_price_evidence(self.conn, 1)
        self.conn.execute(
            "INSERT INTO marketplace_listing_observations(id,listing_id,observed_at,price,currency,status,source_payload_json) "
            "VALUES(2,1,'2026-10-11T10:00:00Z',NULL,'UNKNOWN','active',?)",
            (json.dumps({"announcement":{"price":2,"price_unit":"UNIT","price_type":"FIXED"}}),),
        )
        second = append_ouedkniss_price_evidence(self.conn, 2)
        self.assertNotEqual(first, second)
        rows = self.conn.execute("SELECT id,candidate_amount FROM price_evidence ORDER BY id").fetchall()
        self.assertEqual([row["candidate_amount"] for row in rows], [1.0, 2.0])
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE price_evidence SET candidate_amount=99 WHERE id=?", (first,))

    def test_low_amount_is_not_rejected_as_suspicious_or_outlier(self) -> None:
        low = assess_ouedkniss_price({
            "price": 0.1, "priceUnit": "MILLION", "priceType": "FIXED", "currency": "DZD"
        })
        self.assertAlmostEqual(low.candidate_amount, 100_000.0)
        self.assertNotEqual(low.quality_status, "SUSPICIOUS_PLACEHOLDER")
        self.assertFalse(any("OUTLIER" in reason or "LOW_PRICE" in reason for reason in low.reason_codes))

    def test_only_recognized_currency_codes_are_source_confirmed(self) -> None:
        valid = assess_ouedkniss_price({
            "price": 1200, "priceUnit": "DZD", "priceType": "FIXED", "currency": "DZD"
        })
        self.assertEqual(valid.currency_code, "DZD")
        self.assertEqual(valid.currency_basis, "SOURCE_CONFIRMED")

        unrecognized = assess_ouedkniss_price({
            "price": 1200, "priceUnit": "DZD", "priceType": "FIXED", "currency": "XYZ"
        })
        self.assertIsNone(unrecognized.currency_code)
        self.assertEqual(unrecognized.currency_basis, "UNKNOWN")
        self.assertIn("CURRENCY_CODE_UNRECOGNIZED", unrecognized.reason_codes)
        self.assertEqual(unrecognized.evidence["source_currency"], "XYZ")

    def test_unrecognized_review_currency_is_retained_but_never_trusted(self) -> None:
        seed_ouedkniss(self.conn, 1, 1, "2026-10-10T10:00:00Z", amount=100, unit="DZD")
        evidence_id = append_ouedkniss_price_evidence(self.conn, 1)
        append_price_evidence_review(
            self.conn, evidence_id, decision="NEEDS_MORE_EVIDENCE", reviewer="reviewer-1",
            reviewed_at="2026-10-10T12:00:00Z", quality_status="VALID",
            currency_code="XYZ", currency_basis="INDEPENDENTLY_VERIFIED",
            unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
            product_identity_key="fixture:unknown-currency", price_type="REGULAR",
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)
        ).fetchone()[0], 0)
        review = self.conn.execute(
            "SELECT currency_code,currency_basis,reason_codes_json,supporting_evidence_json "
            "FROM price_evidence_reviews WHERE price_evidence_id=?", (evidence_id,)
        ).fetchone()
        self.assertEqual(review[0:2], (None, "UNKNOWN"))
        self.assertIn("CURRENCY_CODE_UNRECOGNIZED", review[2])
        self.assertEqual(json.loads(review[3])["unrecognized_currency_code_raw"], "XYZ")

    def _constructed_facebook_extraction(self, currency: str | None, basis: str, key: str) -> LandingPageExtraction:
        candidate = PriceCandidate(
            source_entity_key=key,
            raw_value={"price": "1200", "currency": currency},
            raw_price_text=f"1200 {currency or ''}".strip(),
            candidate_amount=1200.0,
            source_unit="ITEM",
            unit_status="NOT_APPLICABLE",
            currency_code=currency,
            currency_basis=basis,
            price_type="REGULAR",
            product_identity_state="VERIFIED",
            extraction_status="SUCCESS",
            quality_status="VALID",
            product_key=f"verified:{key}",
            evidence={"constructed_test_input": True},
        )
        return LandingPageExtraction(
            extraction_status="SUCCESS",
            candidates=(candidate,),
            reason_codes=(),
            evidence={"fixture_source": "caller-constructed"},
            method="fixture_manual_candidate",
            version="test-v1",
        )

    def _append_constructed_facebook(
        self, currency: str | None, basis: str, index: int,
        *, source_currency: str | None | object = ...,
    ) -> int:
        ad_id, observation_id = 1000 + index, 2000 + index
        seed_facebook(
            self.conn,
            ad_id=ad_id,
            observation_id=observation_id,
            url=f"https://shop.example/item-{index}",
        )
        raw_source_currency = currency if source_currency is ... else source_currency
        offer = {"@type": "Offer", "price": "1200"}
        if raw_source_currency is not None:
            offer["priceCurrency"] = raw_source_currency
        page = '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org",
            "@type": "Product",
            "@id": f"sku:constructed-{index}",
            "offers": offer,
        }) + "</script>"
        fetch_id = append_landing_page_fetch(
            self.conn,
            observation_id,
            fetched_at=f"2026-10-12T10:{index:02d}:00Z",
            fetch_status="FETCHED",
            content=page,
        )
        return append_facebook_price_evidence(
            self.conn,
            fetch_id,
            self._constructed_facebook_extraction(currency, basis, f"constructed-{index}"),
        )[0]

    def test_worker_027_forged_facebook_currency_cannot_become_trusted(self) -> None:
        with self.assertRaisesRegex(TypeError, "LandingPageExtraction"):
            append_facebook_price_evidence(self.conn, 0, object())  # type: ignore[arg-type]
        evidence_id = self._append_constructed_facebook("XYZ", "INDEPENDENTLY_VERIFIED", 1)
        stored = self.conn.execute(
            "SELECT currency_code,currency_basis,reason_codes_json,supporting_evidence_json "
            "FROM price_evidence WHERE id=?",
            (evidence_id,),
        ).fetchone()
        self.assertEqual(tuple(stored[:2]), (None, "UNKNOWN"))
        self.assertIn("CURRENCY_CODE_UNRECOGNIZED", stored[2])
        self.assertEqual(
            json.loads(stored[3])["candidate"]["offer_jsonld"]["priceCurrency"], "XYZ"
        )

        assert_approval_unavailable(self,
            self.conn,
            evidence_id,
            decision="APPROVED",
            reviewer="reviewer-1",
            reviewed_at="2026-10-12T11:00:00Z",
            quality_status="VALID",
            unit_status="NOT_APPLICABLE",
            product_identity_state="VERIFIED",
            product_identity_key="verified:constructed-1",
            price_type="REGULAR",
        )
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)
        ).fetchone())

    def test_caller_submitted_pages_never_establish_supported_currency(self) -> None:
        for index, code in enumerate(("dzd", "eur", "usd"), start=10):
            with self.subTest(currency=code):
                evidence_id = self._append_constructed_facebook(code, "SOURCE_CONFIRMED", index)
                stored = self.conn.execute(
                    "SELECT currency_code,currency_basis FROM price_evidence WHERE id=?", (evidence_id,)
                ).fetchone()
                self.assertEqual(tuple(stored), (None, "UNKNOWN"))
                assert_approval_unavailable(self,
                    self.conn, evidence_id, decision="APPROVED", reviewer="reviewer-1",
                    reviewed_at=f"2026-10-12T12:{index:02d}:00Z", quality_status="VALID",
                    unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
                    product_identity_key=f"verified:constructed-{index}", price_type="REGULAR",
                )
                trusted = self.conn.execute(
                    "SELECT currency_code FROM trusted_price_evidence WHERE price_evidence_id=?",
                    (evidence_id,),
                ).fetchone()
                self.assertIsNone(trusted)

    def test_malformed_and_unsupported_candidate_currency_is_preserved_but_unconfirmed(self) -> None:
        for index, raw_code in enumerate(("XYZ", "US$", "AB", "DZDollars"), start=30):
            with self.subTest(currency=raw_code):
                evidence_id = self._append_constructed_facebook(
                    raw_code, "INDEPENDENTLY_VERIFIED", index
                )
                row = self.conn.execute(
                    "SELECT currency_code,currency_basis,supporting_evidence_json FROM price_evidence WHERE id=?",
                    (evidence_id,),
                ).fetchone()
                self.assertEqual(tuple(row[:2]), (None, "UNKNOWN"))
                candidate_evidence = json.loads(row[2])["candidate"]
                self.assertEqual(candidate_evidence["offer_jsonld"]["priceCurrency"], raw_code)

    def test_worker_029_currency_claims_remain_untrusted_without_services(self) -> None:
        missing_id = self._append_constructed_facebook(
            "DZD", "INDEPENDENTLY_VERIFIED", 801, source_currency=None
        )
        missing = self.conn.execute(
            "SELECT currency_code,currency_basis,supporting_evidence_json FROM price_evidence WHERE id=?",
            (missing_id,),
        ).fetchone()
        self.assertEqual(tuple(missing[:2]), (None, "UNKNOWN"))
        self.assertTrue(json.loads(missing[2])["fetch_provenance"]["content_hash_integrity_verified"])
        assert_approval_unavailable(self,
            self.conn, missing_id, decision="APPROVED", reviewer="authorized-reviewer",
            reviewed_at="2026-10-12T15:01:00Z", quality_status="VALID",
            currency_code="DZD", currency_basis="INDEPENDENTLY_VERIFIED",
            unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
            product_identity_key="reviewed:missing-currency", price_type="REGULAR",
            evidence=independent_currency_evidence("DZD"),
        )
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (missing_id,)
        ).fetchone())

        contradictory_id = self._append_constructed_facebook(
            "DZD", "SOURCE_CONFIRMED", 802, source_currency="XYZ"
        )
        contradictory = self.conn.execute(
            "SELECT currency_code,currency_basis,reason_codes_json FROM price_evidence WHERE id=?",
            (contradictory_id,),
        ).fetchone()
        self.assertEqual(tuple(contradictory[:2]), (None, "UNKNOWN"))
        self.assertIn("CURRENCY_CODE_UNRECOGNIZED", contradictory[2])
        assert_approval_unavailable(self,
            self.conn, contradictory_id, reviewer="authorized-reviewer",
            reviewed_at="2026-10-12T15:02:00Z", quality_status="VALID",
            currency_code="DZD", currency_basis="SOURCE_CONFIRMED",
            unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
            product_identity_key="reviewed:contradictory-currency", price_type="REGULAR",
        )
        assert_approval_unavailable(self,
            self.conn, contradictory_id, decision="APPROVED", reviewer="authorized-reviewer",
            reviewed_at="2026-10-12T15:03:00Z", quality_status="VALID",
            currency_code="DZD", currency_basis="INDEPENDENTLY_VERIFIED",
            unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
            product_identity_key="reviewed:contradictory-currency", price_type="REGULAR",
            evidence=independent_currency_evidence("DZD"),
        )
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (contradictory_id,)
        ).fetchone())

    def test_malformed_candidate_amount_is_rejected_at_api_and_database_boundaries(self) -> None:
        seed_facebook(self.conn, ad_id=900, observation_id=2900)
        html = fixture("normal_numeric.html")
        fetch_id = append_landing_page_fetch(
            self.conn, 2900, fetched_at="2026-10-12T16:00:00Z", fetch_status="FETCHED", content=html,
        )
        extracted = extract_landing_page_prices(html)
        invalid_amounts = (
            "not-a-number", "100", b"100", True, False,
            float("nan"), float("inf"), float("-inf"),
            0, -1, 1_000_000_000_001,
        )
        for amount in invalid_amounts:
            with self.subTest(candidate_amount=amount):
                malformed_candidate = replace(extracted.candidates[0], candidate_amount=amount)
                malformed_extraction = LandingPageExtraction(
                    extraction_status="SUCCESS", candidates=(malformed_candidate,), reason_codes=(), evidence={},
                )
                with self.assertRaises(ValueError):
                    append_facebook_price_evidence(self.conn, fetch_id, malformed_extraction)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM price_evidence").fetchone()[0], 0)

        valid_id = append_facebook_price_evidence(self.conn, fetch_id, extracted)[0]
        limit_candidate = replace(extracted.candidates[0], candidate_amount=1_000_000_000_000)
        self.assertEqual(append_facebook_price_evidence(
            self.conn, fetch_id, replace(extracted, candidates=(limit_candidate,))
        )[0], valid_id)
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(price_evidence)")]
        values = dict(zip(columns, self.conn.execute(
            "SELECT * FROM price_evidence WHERE id=?", (valid_id,)
        ).fetchone()))
        values.pop("id", None)
        values["idempotency_key"] = "malformed-text-amount-lower-level"
        values["candidate_amount"] = "not-a-number"
        names = list(values)
        insert_sql = (
            f"INSERT INTO price_evidence ({','.join(names)}) "
            f"VALUES ({','.join('?' for _ in names)})"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "positive-price policy"):
            self.conn.execute(insert_sql, tuple(values[name] for name in names))

        # Even if a lower-level writer removes the trigger, SQLite storage-class
        # filtering keeps malformed text out of the trusted view.
        self.conn.execute("DROP TRIGGER trg_price_evidence_amount_policy")
        self.conn.execute(insert_sql, tuple(values[name] for name in names))
        malformed_id = self.conn.execute(
            "SELECT id FROM price_evidence WHERE idempotency_key=?",
            ("malformed-text-amount-lower-level",),
        ).fetchone()[0]
        self.assertEqual(self.conn.execute(
            "SELECT typeof(candidate_amount) FROM price_evidence WHERE id=?", (malformed_id,)
        ).fetchone()[0], "text")
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (malformed_id,)
        ).fetchone())

    def test_sqlite_amount_affinity_cannot_recover_original_python_types(self) -> None:
        seed_facebook(self.conn, ad_id=950, observation_id=2950)
        html = fixture("normal_numeric.html")
        fetch_id = append_landing_page_fetch(
            self.conn, 2950, fetched_at="2026-10-12T16:30:00Z", fetch_status="FETCHED", content=html,
        )
        base_id = append_facebook_price_evidence(
            self.conn, fetch_id, extract_landing_page_prices(html)
        )[0]
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(price_evidence)")]
        base = dict(zip(columns, self.conn.execute(
            "SELECT * FROM price_evidence WHERE id=?", (base_id,)
        ).fetchone()))
        base.pop("id", None)
        names = list(base)
        sql = f"INSERT INTO price_evidence ({','.join(names)}) VALUES ({','.join('?' for _ in names)})"
        probes = (
            (True, True, "real", 1.0),
            (False, False, None, None),
            (100, True, "real", 100.0),
            (123.5, True, "real", 123.5),
            ("100", True, "real", 100.0),
            (b"100", False, None, None),
            (float("nan"), True, "null", None),
            (float("inf"), False, None, None),
            (float("-inf"), False, None, None),
            (0, False, None, None),
            (-1, False, None, None),
            (1_000_000_000_000, True, "real", 1_000_000_000_000.0),
            (1_000_000_000_001, False, None, None),
        )
        for index, (value, accepted, expected_type, expected_value) in enumerate(probes):
            with self.subTest(value=repr(value)):
                row_values = dict(base)
                row_values["candidate_amount"] = value
                row_values["idempotency_key"] = f"sqlite-affinity-{index}"
                try:
                    self.conn.execute(sql, tuple(row_values[name] for name in names))
                except sqlite3.IntegrityError:
                    self.assertFalse(accepted)
                    continue
                self.assertTrue(accepted)
                stored = self.conn.execute(
                    "SELECT typeof(candidate_amount),candidate_amount FROM price_evidence WHERE idempotency_key=?",
                    (f"sqlite-affinity-{index}",),
                ).fetchone()
                self.assertEqual(tuple(stored), (expected_type, expected_value))
                self.assertIsNone(self.conn.execute(
                    "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id="
                    "(SELECT id FROM price_evidence WHERE idempotency_key=?)",
                    (f"sqlite-affinity-{index}",),
                ).fetchone())

        seed_ouedkniss(self.conn, 2, 2, "2026-10-13T10:00:00Z", amount=1_000_000_000_000)
        maximum_id = append_ouedkniss_price_evidence(self.conn, 2)
        self.assertEqual(self.conn.execute(
            "SELECT candidate_amount FROM price_evidence WHERE id=?", (maximum_id,)
        ).fetchone()[0], 1_000_000_000_000.0)

    def test_database_currency_guards_and_trusted_view_reject_lower_level_forgery(self) -> None:
        evidence_id = self._append_constructed_facebook("DZD", "SOURCE_CONFIRMED", 70)
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(price_evidence)")]
        values = dict(zip(columns, self.conn.execute(
            "SELECT * FROM price_evidence WHERE id=?", (evidence_id,)
        ).fetchone()))
        values.pop("id", None)
        values.update(currency_code="XYZ", currency_basis="INDEPENDENTLY_VERIFIED", idempotency_key="direct-invalid-currency")
        names = list(values)
        with self.assertRaises(sqlite3.Error):
            self.conn.execute(
                f"INSERT INTO price_evidence ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                tuple(values[name] for name in names),
            )
        with self.assertRaises(sqlite3.Error):
            self.conn.execute(
                """INSERT INTO price_evidence_reviews (
                    price_evidence_id,decision,quality_status,currency_code,currency_basis,
                    unit_status,product_identity_state,product_identity_key,price_type,
                    reviewer,reviewed_at,reason_codes_json,supporting_evidence_json,idempotency_key
                ) VALUES (?, 'APPROVED','VALID','XYZ','INDEPENDENTLY_VERIFIED',
                    'NOT_APPLICABLE','VERIFIED','verified:lower-level','REGULAR',
                    'reviewer','2026-10-12T13:00:00Z','[]','{}','direct-invalid-review')""",
                (evidence_id,),
            )

        # Simulate a malformed row introduced below the normal database write guards.
        self.conn.execute("DROP TRIGGER trg_price_evidence_currency_allowlist")
        self.conn.execute("DROP TRIGGER trg_price_evidence_reviews_currency_allowlist")
        self.conn.execute("DROP TRIGGER trg_price_evidence_reviews_authorization")
        self.conn.execute("DROP TRIGGER trg_price_evidence_no_update")
        self.conn.execute(
            "UPDATE price_evidence SET currency_code='XYZ',currency_basis='INDEPENDENTLY_VERIFIED' WHERE id=?",
            (evidence_id,),
        )
        self.conn.execute(
            """INSERT INTO price_evidence_reviews (
                price_evidence_id,decision,quality_status,currency_code,currency_basis,
                unit_status,product_identity_state,product_identity_key,price_type,
                reviewer,reviewed_at,reason_codes_json,supporting_evidence_json,idempotency_key
            ) VALUES (?, 'APPROVED','VALID','XYZ','INDEPENDENTLY_VERIFIED',
                'NOT_APPLICABLE','VERIFIED','verified:lower-level','REGULAR',
                'reviewer','2026-10-12T13:01:00Z','[]','{}','forged-valid-shape-review')""",
            (evidence_id,),
        )
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)
        ).fetchone())

    def test_low_valid_point_price_remains_untrusted_without_review_service(self) -> None:
        seed_ouedkniss(
            self.conn, 1, 1, "2026-10-10T10:00:00Z", amount=0.01,
            unit="DZD", price_type="FIXED",
        )
        self.conn.execute(
            """INSERT INTO price_evidence (
                source_platform,source_entity_key,marketplace_observation_id,
                source_observed_at,price_observed_at,raw_value_json,raw_price_text,
                candidate_amount,source_unit,unit_status,currency_code,currency_basis,
                price_type,product_identity_state,product_identity_key,extraction_method,extraction_version,
                extracted_at,extraction_status,quality_status,reason_codes_json,
                supporting_evidence_json,idempotency_key
            ) VALUES ('Ouedkniss','listing-1',1,'2026-10-10T10:00:00Z','2026-10-10T10:00:00Z',
                '0.01','0.01',0.01,'DZD','NOT_APPLICABLE','DZD','SOURCE_CONFIRMED',
                'REGULAR','VERIFIED','fixture:low-priced-item','fixture-review','v1','2026-10-10T11:00:00Z',
                'SUCCESS','VALID','[]','{}','valid-low-fixture')"""
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)
        assert_approval_unavailable(self,
            self.conn, 1, decision="APPROVED", reviewer="reviewer-1",
            reviewed_at="2026-10-10T12:00:00Z", quality_status="VALID",
            currency_code="DZD", currency_basis="SOURCE_CONFIRMED",
            unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
            product_identity_key="fixture:low-priced-item",
            price_type="REGULAR", evidence={"low_price_reviewed": True},
        )
        row = self.conn.execute("SELECT * FROM trusted_price_evidence").fetchone()
        self.assertIsNone(row)
        self.assertEqual(self.conn.execute(
            "SELECT candidate_amount FROM price_evidence WHERE id=1"
        ).fetchone()[0], 0.01)

    def test_unavailable_approval_leaves_price_evidence_untrusted_and_unchanged(self) -> None:
        observed_at = "2026-10-10T10:00:00Z"
        for index in range(1, 4):
            seed_ouedkniss(self.conn, index, index, observed_at)
        evidence_id = append_ouedkniss_price_evidence(self.conn, 1)
        original = tuple(self.conn.execute(
            "SELECT candidate_amount,currency_code,quality_status,reason_codes_json FROM price_evidence WHERE id=?",
            (evidence_id,),
        ).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)
        review_evidence = independent_currency_evidence("DZD")
        assert_approval_unavailable(self,
            self.conn, evidence_id, decision="APPROVED", reviewer="reviewer-1",
            reviewed_at="2026-10-11T10:00:00Z", quality_status="VALID",
            currency_code="DZD", currency_basis="INDEPENDENTLY_VERIFIED",
            unit_status="CONFIRMED", product_identity_state="VERIFIED",
            product_identity_key="reviewed:product-1", unit_interpretation="DZD per listing item",
            price_type="REGULAR",
            evidence={"manual_review": "fixture-only", **review_evidence},
        )
        trusted = self.conn.execute("SELECT * FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)).fetchone()
        self.assertIsNone(trusted)
        self.assertEqual(original, tuple(self.conn.execute(
            "SELECT candidate_amount,currency_code,quality_status,reason_codes_json FROM price_evidence WHERE id=?",
            (evidence_id,),
        ).fetchone()))

    def test_fetch_and_review_records_are_append_only(self) -> None:
        seed_ouedkniss(self.conn, 1, 1, "2026-10-10T10:00:00Z")
        evidence_id = append_ouedkniss_price_evidence(self.conn, 1)
        review_id = append_price_evidence_review(
            self.conn, evidence_id, decision="NEEDS_MORE_EVIDENCE", reviewer="reviewer-1",
            reviewed_at="2026-10-10T12:00:00Z",
        )
        seed_facebook(self.conn)
        fetch_id = append_landing_page_fetch(
            self.conn, 200, fetched_at="2026-10-11T10:00:00Z", fetch_status="FETCHED",
            content=fixture("normal_numeric.html"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE price_evidence_reviews SET decision='APPROVED' WHERE id=?", (review_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM price_evidence_reviews WHERE id=?", (review_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE facebook_landing_page_fetches SET resolved_url='https://other.test' WHERE id=?", (fetch_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM facebook_landing_page_fetches WHERE id=?", (fetch_id,))

    def test_facebook_fetch_cannot_rebind_content_to_a_different_destination(self) -> None:
        seed_facebook(self.conn, ad_id=77, observation_id=707,
                      url="https://shop.example/observed-item")
        with self.assertRaisesRegex(ValueError, "must match the destination"):
            append_landing_page_fetch(
                self.conn, 707, fetched_at="2026-10-12T17:00:00Z",
                fetch_status="FETCHED", content=fixture("normal_numeric.html"),
                original_url="https://attacker.example/other-item",
            )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM facebook_landing_page_fetches"
        ).fetchone()[0], 0)

    def test_worker_031_fabricated_fetch_bytes_and_claims_stay_untrusted(self) -> None:
        seed_facebook(self.conn, ad_id=91, observation_id=901,
                      url="https://shop.example/observed-item")
        html = fixture("normal_numeric.html")
        fetch_id = append_landing_page_fetch(
            self.conn, 901, fetched_at="2026-10-14T08:00:00Z", fetch_status="FETCHED",
            content=html, resolved_url="https://attacker.example/injected-final-url",
            http_status=200, content_type="text/html; charset=utf-8",
            fetcher_name="trusted-fetcher-v99", fetcher_version="99",
            evidence={"network_io": True, "tls_verified": True},
        )
        fetch = self.conn.execute(
            "SELECT * FROM facebook_landing_page_fetches WHERE id=?", (fetch_id,)
        ).fetchone()
        claims = json.loads(fetch["evidence_json"])
        self.assertEqual(fetch["provenance_status"], "UNVERIFIED")
        self.assertEqual(fetch["fetch_status"], "FAILED")
        self.assertIsNone(fetch["resolved_url"])
        self.assertIsNone(fetch["http_status"])
        self.assertIsNone(fetch["content_type"])
        self.assertEqual(fetch["fetcher_name"], "untrusted_caller_submission")
        self.assertEqual(claims["caller_claims"]["resolved_url"], "https://attacker.example/injected-final-url")
        self.assertEqual(claims["caller_claims"]["fetcher_name"], "trusted-fetcher-v99")
        self.assertTrue(claims["caller_claims"]["metadata"]["tls_verified"])
        self.assertTrue(claims["caller_claims"]["metadata"]["network_io"])
        self.assertEqual(fetch["content_sha256"], hashlib.sha256(html.encode()).hexdigest())
        self.assertFalse(claims["source_origin_authenticated"])
        self.assertEqual(claims["content_sha256_semantics"], "integrity_only_not_origin_authentication")

        extracted = extract_landing_page_prices(html)
        forged_candidate = replace(
            extracted.candidates[0], candidate_amount=1, currency_code="EUR",
            currency_basis="SOURCE_CONFIRMED", product_identity_state="VERIFIED",
            product_key="forged:identity",
        )
        caller_extraction = LandingPageExtraction(
            extraction_status="SUCCESS", candidates=(forged_candidate,), reason_codes=(),
            evidence={"fetcher": "trusted-fetcher-v99", "network_io": True},
        )
        evidence_id = append_facebook_price_evidence(self.conn, fetch_id, caller_extraction)[0]
        evidence = self.conn.execute(
            "SELECT candidate_amount,currency_code,currency_basis,product_identity_state,"
            "supporting_evidence_json FROM price_evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        self.assertEqual(evidence["candidate_amount"], 1299.9)
        self.assertIsNone(evidence["currency_code"])
        self.assertEqual(evidence["currency_basis"], "UNKNOWN")
        self.assertEqual(evidence["product_identity_state"], "UNVERIFIED")
        provenance = json.loads(evidence["supporting_evidence_json"])["fetch_provenance"]
        self.assertFalse(provenance["source_origin_authenticated"])
        self.assertFalse(provenance["content_hash_proves_origin"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM trusted_price_evidence"
        ).fetchone()[0], 0)

    def test_observed_destination_rejects_obvious_ssrf_targets(self) -> None:
        for url in (
            "http://127.0.0.1/admin", "https://[::1]/", "http://10.0.0.8/",
            "http://169.254.1.2/", "http://localhost/", "http://service.local/",
            "http://user:pass@shop.example/", "https://shop.example:8443/",
        ):
            with self.subTest(url=url):
                seed_facebook(self.conn, ad_id=100 + len(url), observation_id=1000 + len(url), url=url)
                with self.assertRaises(ValueError):
                    append_landing_page_fetch(
                        self.conn, 1000 + len(url), fetched_at="2026-10-14T09:00:00Z",
                        fetch_status="FETCHED", content="raw",
                    )

    def test_worker_031_spoofed_sqlite_udf_cannot_authorize_unsigned_approval(self) -> None:
        seed_ouedkniss(
            self.conn, 1, 1, "2026-10-10T10:00:00Z", amount=72000,
            unit="NOT_APPLICABLE", price_type="FIXED",
        )
        evidence_id = append_ouedkniss_price_evidence(self.conn, 1)
        calls = []
        self.conn.create_function(
            "kratzwerk_price_review_authorized", 14,
            lambda *args: calls.append(args) or 1,
        )
        insert = """INSERT INTO price_evidence_reviews (
            price_evidence_id,decision,quality_status,currency_code,currency_basis,
            unit_status,product_identity_state,product_identity_key,price_type,
            reviewer,reviewed_at,reason_codes_json,supporting_evidence_json,idempotency_key
        ) VALUES (?, 'APPROVED','VALID','DZD','SOURCE_CONFIRMED',
            'NOT_APPLICABLE','VERIFIED','spoofed:identity','REGULAR',
            'untrusted-caller','2026-10-14T09:30:00Z','[]','{}','unsigned-spoofed-review')"""
        with self.assertRaisesRegex(sqlite3.IntegrityError, "trusted price review service is not configured"):
            self.conn.execute(insert, (evidence_id,))
        self.assertEqual(calls, [])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM trusted_price_evidence WHERE price_evidence_id=?", (evidence_id,)
        ).fetchone())

    def test_signature_attempts_missing_key_and_nonstandard_review_json_fail_closed(self) -> None:
        seed_ouedkniss(self.conn, 1, 1, "2026-10-10T10:00:00Z", amount=100)
        evidence_id = append_ouedkniss_price_evidence(self.conn, 1)
        seed_ouedkniss(self.conn, 2, 2, "2026-10-10T11:00:00Z", amount=200)
        other_evidence_id = append_ouedkniss_price_evidence(self.conn, 2)
        common = {
            "decision": "APPROVED", "reviewer": "untrusted-caller",
            "reviewed_at": "2026-10-14T10:00:00Z", "quality_status": "VALID",
            "currency_code": "DZD", "currency_basis": "SOURCE_CONFIRMED",
            "unit_status": "NOT_APPLICABLE", "product_identity_state": "VERIFIED",
            "product_identity_key": "spoofed:key", "price_type": "REGULAR",
        }
        with patch.dict(os.environ, {"KRATZWERK_PRICE_REVIEW_HMAC_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "no trusted review service"):
                append_price_evidence_review(self.conn, evidence_id, **common)
        # The API has no signature parameter: invalid, altered-field, and replayed
        # signatures cannot be submitted through the evidence caller boundary.
        attempts = (
            (evidence_id, common, "invalid-signature"),
            (evidence_id, {**common, "product_identity_key": "changed-after-signing"}, "signature-for-other-fields"),
            (other_evidence_id, common, "replayed-signature"),
        )
        for target_id, review_fields, signature in attempts:
            with self.subTest(signature=signature):
                with self.assertRaisesRegex(TypeError, "authorization_signature"):
                    append_price_evidence_review(
                        self.conn, target_id, **review_fields, authorization_signature=signature
                    )
        with self.assertRaises(ValueError):
            append_price_evidence_review(
                self.conn, evidence_id, decision="NEEDS_MORE_EVIDENCE", reviewer="reviewer",
                reviewed_at="2026-10-14T10:01:00Z", evidence={"measurement": float("nan")},
            )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM price_evidence_reviews"
        ).fetchone()[0], 0)


class FacebookOfflineExtractionTests(unittest.TestCase):
    def extract(self, name: str, **kwargs):
        return extract_landing_page_prices(fixture(name), **kwargs)

    def test_normal_numeric_and_explicit_currency(self) -> None:
        result = self.extract("normal_numeric.html", verified_product_evidence={
            "@id:sku:prod-1": {"review": "manual fixture identity review", "basis": "exact SKU"}
        })
        candidate = result.candidates[0]
        self.assertEqual(candidate.candidate_amount, 1299.9)
        self.assertEqual(candidate.currency_code, "DZD")
        self.assertEqual(candidate.currency_basis, "SOURCE_CONFIRMED")
        self.assertEqual(candidate.product_identity_state, "VERIFIED")
        self.assertEqual(candidate.quality_status, "VALID")

    def test_french_european_number_format(self) -> None:
        candidate = self.extract("french_european.html").candidates[0]
        self.assertEqual(candidate.candidate_amount, 1299.9)
        self.assertEqual(candidate.currency_code, "EUR")

    def test_explicit_dzd_eur_usd_are_preserved_across_candidates(self) -> None:
        result = self.extract("explicit_currencies.html")
        self.assertEqual([c.currency_code for c in result.candidates], ["DZD", "EUR", "USD"])

    def test_unrecognized_three_letter_price_currency_is_not_confirmed(self) -> None:
        document = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Fixture item",
            "sku": "fixture-1",
            "offers": {"@type": "Offer", "price": "1200", "priceCurrency": "XYZ"},
        }
        html = '<script type="application/ld+json">' + json.dumps(document) + "</script>"
        candidate = extract_landing_page_prices(html).candidates[0]
        self.assertIsNone(candidate.currency_code)
        self.assertEqual(candidate.currency_basis, "UNKNOWN")
        self.assertEqual(candidate.quality_status, "UNIT_OR_CURRENCY_UNCONFIRMED")
        self.assertIn("CURRENCY_CODE_UNRECOGNIZED", candidate.reason_codes)
        self.assertEqual(candidate.evidence["offer_jsonld"]["priceCurrency"], "XYZ")

    def test_missing_or_ambiguous_currency_never_becomes_dzd(self) -> None:
        missing = self.extract("missing_currency.html").candidates[0]
        ambiguous = self.extract("ambiguous_currency.html").candidates[0]
        self.assertIsNone(missing.currency_code)
        self.assertEqual(missing.quality_status, "UNIT_OR_CURRENCY_UNCONFIRMED")
        self.assertIsNone(ambiguous.currency_code)
        self.assertIn("CURRENCY_SYMBOL_AMBIGUOUS", ambiguous.reason_codes)

    def test_sale_and_regular_price_specifications_remain_separate(self) -> None:
        result = self.extract("sale_regular.html")
        by_type = {candidate.price_type: candidate.candidate_amount for candidate in result.candidates}
        self.assertEqual(by_type, {"REGULAR": 120.0, "SALE": 90.0})

    def test_range_is_not_collapsed_to_a_point_price(self) -> None:
        candidate = self.extract("range.html").candidates[0]
        self.assertEqual(candidate.price_type, "RANGE")
        self.assertEqual((candidate.candidate_amount, candidate.candidate_amount_max), (1000.0, 2500.0))

    def test_starting_from_price_keeps_its_type(self) -> None:
        candidate = self.extract("starting_from.html").candidates[0]
        self.assertEqual(candidate.candidate_amount, 2500.0)
        self.assertEqual(candidate.price_type, "STARTING_FROM")

    def test_variants_and_multiple_products_are_all_returned_as_unresolved(self) -> None:
        variants = self.extract("variants.html")
        self.assertEqual(len(variants.candidates), 2)
        self.assertTrue(all(c.product_identity_state == "AMBIGUOUS" for c in variants.candidates))
        multiple = self.extract("multiple_products.html")
        self.assertEqual(len(multiple.candidates), 2)
        self.assertTrue(all(c.product_identity_state == "AMBIGUOUS" for c in multiple.candidates))
        self.assertEqual(len({c.candidate_amount for c in multiple.candidates}), 2)

    def test_homepage_inaccessible_missing_and_malformed_content(self) -> None:
        category = extract_landing_page_prices(fixture("category.html"))
        no_price = self.extract("no_price_offer.html")
        inaccessible = extract_landing_page_prices(None, fetch_status="INACCESSIBLE")
        missing = extract_landing_page_prices(None)
        malformed = self.extract("malformed.html")
        self.assertEqual(category.extraction_status, "MISSING")
        self.assertEqual(no_price.candidates[0].quality_status, "MISSING")
        self.assertEqual(no_price.candidates[0].extraction_status, "MISSING")
        self.assertEqual(inaccessible.extraction_status, "ERROR")
        self.assertEqual(missing.candidates[0].quality_status, "EXTRACTION_ERROR")
        self.assertEqual(malformed.extraction_status, "ERROR")

    def test_unverified_target_is_not_trusted_and_fetch_time_is_separate_from_ad_time(self) -> None:
        temp = tempfile.TemporaryDirectory()
        conn = make_database(Path(temp.name) / "fb.sqlite")
        try:
            seed_facebook(conn, observed_at="2026-10-01T10:00:00Z")
            html = fixture("normal_numeric.html")
            fetch_id = append_landing_page_fetch(
                conn, 200, fetched_at="2026-10-11T10:00:00Z", fetch_status="FETCHED",
                content=html, resolved_url="https://shop.example/products/prod-1",
                content_type="text/html",
            )
            result = extract_landing_page_prices(html, source_entity_key="library-20")
            evidence_id = append_facebook_price_evidence(conn, fetch_id, result, extracted_at="2026-10-11T10:01:00Z")[0]
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)
            self.assertEqual(append_facebook_price_evidence(conn, fetch_id, result)[0], evidence_id)
            row = conn.execute("SELECT source_observed_at,price_observed_at FROM price_evidence WHERE id=?", (evidence_id,)).fetchone()
            fetch_record = conn.execute(
                "SELECT fetched_at,evidence_json FROM facebook_landing_page_fetches WHERE id=?",
                (fetch_id,),
            ).fetchone()
            self.assertEqual(row[0], "2026-10-01T10:00:00Z")
            self.assertEqual(row[1], fetch_record["fetched_at"])
            self.assertEqual(
                json.loads(fetch_record["evidence_json"])["caller_claims"]["fetched_at"],
                "2026-10-11T10:00:00Z",
            )
            verified = extract_landing_page_prices(
                html, source_entity_key="library-20", verified_product_evidence={
                    "@id:sku:prod-1": {"review": "fixture manual identity check", "basis": "exact SKU"}
                }
            )
            later_fetch = append_landing_page_fetch(
                conn, 200, fetched_at="2026-10-12T10:00:00Z", fetch_status="FETCHED",
                content=html, resolved_url="https://shop.example/products/prod-1",
            )
            later_evidence = append_facebook_price_evidence(conn, later_fetch, verified)[0]
            assert_approval_unavailable(self,
                conn, later_evidence, decision="APPROVED", reviewer="reviewer-1",
                reviewed_at="2026-10-12T10:05:00Z", quality_status="VALID",
                currency_code="DZD", currency_basis="SOURCE_CONFIRMED",
                unit_status="NOT_APPLICABLE", product_identity_state="VERIFIED",
                product_identity_key="@id:sku:prod-1",
                price_type="REGULAR", evidence={"target_identity": "exact fixture SKU"},
            )
            view = conn.execute("SELECT * FROM trusted_price_evidence WHERE price_evidence_id=?", (later_evidence,)).fetchone()
            self.assertIsNone(view)
            self.assertNotEqual(evidence_id, later_evidence)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_evidence").fetchone()[0], 2)
        finally:
            conn.close()
            temp.cleanup()

    def test_multi_product_page_candidates_are_never_trusted_by_result_order(self) -> None:
        temp = tempfile.TemporaryDirectory()
        conn = make_database(Path(temp.name) / "multi-product.sqlite")
        try:
            seed_facebook(conn)
            html = fixture("multiple_products.html")
            fetch_id = append_landing_page_fetch(
                conn, 200, fetched_at="2026-10-11T10:00:00Z", fetch_status="FETCHED",
                content=html,
            )
            result = extract_landing_page_prices(html, source_entity_key="library-20")
            ids = append_facebook_price_evidence(conn, fetch_id, result)
            self.assertEqual(len(ids), 2)
            states = conn.execute(
                "SELECT product_identity_state,quality_status FROM price_evidence ORDER BY id"
            ).fetchall()
            self.assertTrue(all(row["product_identity_state"] == "UNVERIFIED" for row in states))
            self.assertTrue(all(row["quality_status"] == "AMBIGUOUS" for row in states))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trusted_price_evidence").fetchone()[0], 0)
        finally:
            conn.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
