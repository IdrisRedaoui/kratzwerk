from __future__ import annotations

import copy
import json
from contextlib import contextmanager
import sqlite3
import unittest
from unittest.mock import patch

from sensors import facebook_ads_sensor as sensor


def source_ad(
    *,
    library_id="900100200300",
    url=None,
    page_name="Page Example",
    creative_text="A new campaign",
    status="Active",
    raw_payload=None,
    **overrides,
):
    if url is None and library_id is not None:
        url = f"https://www.facebook.com/ads/library/?id={library_id}"
    row = {
        "library_id": library_id,
        "url": url,
        "page_name": page_name,
        "page_source_id": "page-22",
        "platforms": ["Facebook", "Instagram"],
        "creative_text": creative_text,
        "image_urls": ["https://cdn.example.test/creative.jpg"] if creative_text else [],
        "video_urls": [],
        "landing_url": "https://shop.example.test/item",
        "start_date_text": "Oct 2, 2026",
        "source_status": status,
        "raw_payload": raw_payload if raw_payload is not None else {
            "id": library_id,
            "card_text": "Library ID: " + str(library_id),
            "opaque": {"original": True},
        },
    }
    row.update(overrides)
    return row


def captured_facebook_card_fixture():
    """Representative card text/buttons copied from captured Facebook evidence."""
    creative = (
        "Découvrez les opportunités d'emploi dans le domaine de la peinture. Explorez les "
        "missions, les compétences recherchées et les postes qui pourraient vous convenir."
    )
    return {
        "text": (
            "\u200b\nInactive\nLibrary ID: 1834446447534350\nJul 1, 2026 - Aug 7, 2026\n"
            "Platforms\n\u200b\nEU transparency\n\u200b\nOpen Dropdown\n\u200b\nSee ad details\n"
            "QuickNest\nSponsored • Paid for by Interdog Media Limited and others\n"
            f"{creative}\n5TING.COM\nDécouvrez les emplois de peintre\nLearn more"
        ),
        "links": [{
            "href": "https://web.facebook.com/goidemsuning/",
            "text": "QuickNest",
            "aria_label": None,
        }],
        "buttons": [
            {"text": "Open Dropdown\n\u200b", "aria_label": None},
            {"text": "See ad details", "aria_label": None},
            {"text": creative, "aria_label": None},
            {"text": "5TING.COM", "aria_label": None},
            {"text": "Découvrez les emplois de peintre", "aria_label": None},
            {"text": "", "aria_label": None},
            {"text": "", "aria_label": None},
            {"text": "Learn more", "aria_label": None},
        ],
        "images": [],
        "videos": [],
        "attributes": {},
        "platform_labels": [],
    }


class FacebookAdsSensorV2Tests(unittest.TestCase):
    def setUp(self):
        self.db_uri = f"file:facebook-v2-{id(self)}?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.db_uri, uri=True)
        self._create_v2_schema()

    def tearDown(self):
        self.anchor.close()

    def _connect(self):
        conn = sqlite3.connect(self.db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def opened_database(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _create_v2_schema(self):
        self.anchor.executescript(
            """
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY,
                    canonical_name TEXT NOT NULL
                );
                CREATE TABLE facebook_ads (
                    id INTEGER PRIMARY KEY,
                    library_id TEXT,
                    url TEXT,
                    page_source_id TEXT,
                    page_name TEXT,
                    platform TEXT NOT NULL DEFAULT 'facebook',
                    platforms_json TEXT NOT NULL DEFAULT '[\"facebook\"]',
                    creative_text TEXT,
                    creative_fingerprint TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    start_date TEXT,
                    status TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (status IN ('active', 'inactive', 'unknown')),
                    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    source_payload_json TEXT NOT NULL DEFAULT '{}'
                        CHECK (json_valid(source_payload_json)),
                    CHECK (json_valid(platforms_json)),
                    UNIQUE (platform, library_id)
                );
                CREATE UNIQUE INDEX ux_facebook_ads_url ON facebook_ads(url)
                    WHERE url IS NOT NULL;
                CREATE TABLE facebook_ad_observations (
                    id INTEGER PRIMARY KEY,
                    ad_id INTEGER NOT NULL REFERENCES facebook_ads(id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    active INTEGER CHECK (active IN (0, 1) OR active IS NULL),
                    detected_metadata_json TEXT NOT NULL DEFAULT '{}'
                        CHECK (json_valid(detected_metadata_json)),
                    creative_fingerprint TEXT,
                    UNIQUE (ad_id, observed_at)
                );
                CREATE TRIGGER observation_no_update
                BEFORE UPDATE ON facebook_ad_observations
                BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
                CREATE TRIGGER observation_no_delete
                BEFORE DELETE ON facebook_ad_observations
                BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
                CREATE TABLE legacy_advertising_campaigns (id INTEGER PRIMARY KEY, note TEXT);
                INSERT INTO legacy_advertising_campaigns VALUES (1, 'preserve this row');
            """
        )

    def capture(self, ads, timestamp, **kwargs):
        return sensor.store_capture(
            ads,
            timestamp,
            db_connect=self._connect,
            **kwargs,
        )

    def test_new_ad_creates_one_canonical_row_and_one_observation(self):
        stats = self.capture([source_ad()], "2026-10-06T10:00:00Z")
        self.assertEqual((stats.new_ads, stats.observations), (1, 1))
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 1)

    def test_same_ad_twice_reuses_canonical_row_and_appends_observation(self):
        self.capture([source_ad()], "2026-10-06T10:00:00Z")
        stats = self.capture([source_ad()], "2026-10-06T11:00:00Z")
        self.assertEqual((stats.new_ads, stats.existing_ads, stats.observations), (0, 1, 1))
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 2)

    def test_stable_library_id_resolves_row_even_when_ad_url_changes(self):
        self.capture([source_ad()], "2026-10-06T10:00:00Z")
        changed_url = "https://www.facebook.com/ads/library/?id=900100200300&ref=updated"
        stats = self.capture(
            [source_ad(url=changed_url, creative_text="Updated creative")],
            "2026-10-06T11:00:00Z",
        )
        with self.opened_database() as conn:
            self.assertEqual(stats.existing_ads, 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)
            ad = conn.execute("SELECT library_id, url FROM facebook_ads").fetchone()
            self.assertEqual(ad["library_id"], "900100200300")
            self.assertEqual(ad["url"], "https://www.facebook.com/ads/library/?id=900100200300")

    def test_url_fallback_resolves_when_stable_id_is_unavailable(self):
        fallback = source_ad(
            library_id=None,
            url="https://www.facebook.com/ads/library/?ad_ref=creative-abc&utm_source=feed",
        )
        first = self.capture([fallback], "2026-10-06T10:00:00Z")
        second = self.capture([fallback], "2026-10-06T11:00:00Z")
        self.assertEqual(first.new_ads, 1)
        self.assertEqual(second.existing_ads, 1)
        with self.opened_database() as conn:
            ad = conn.execute("SELECT library_id, url FROM facebook_ads").fetchone()
            self.assertIsNone(ad["library_id"])
            self.assertEqual(
                ad["url"],
                "https://www.facebook.com/ads/library/?ad_ref=creative-abc",
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)

    def test_library_id_in_ad_url_is_promoted_to_stable_identity(self):
        ad = source_ad(library_id=None, url="https://www.facebook.com/ads/library/?id=445566")
        normalized = sensor.normalize_ad(ad)
        self.assertEqual(normalized["library_id"], "445566")
        self.assertEqual(normalized["url"], "https://www.facebook.com/ads/library/?id=445566")

    def test_stable_id_reuses_migrated_url_only_row_and_backfills_identity(self):
        canonical_url = "https://www.facebook.com/ads/library/?id=900100200300"
        with self.opened_database() as conn:
            conn.execute(
                """INSERT INTO facebook_ads
                   (id, library_id, url, platform, first_seen_at, last_seen_at, status, source_payload_json)
                   VALUES (91, NULL, ?, 'facebook', '2026-01-01T00:00:00Z',
                           '2026-01-02T00:00:00Z', 'unknown', '{\"legacy_campaign_id\":91}')""",
                (canonical_url,),
            )
            conn.commit()

        stats = self.capture([source_ad()], "2026-10-06T10:00:00Z")

        self.assertEqual((stats.new_ads, stats.existing_ads, stats.observations), (0, 1, 1))
        with self.opened_database() as conn:
            rows = conn.execute("SELECT * FROM facebook_ads").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], 91)
            self.assertEqual(rows[0]["library_id"], "900100200300")
            self.assertEqual(rows[0]["first_seen_at"], "2026-01-01T00:00:00Z")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 1)

    def test_missing_optional_fields_remain_null(self):
        ad = {
            "library_id": "900100200300",
            "url": "https://www.facebook.com/ads/library/?id=900100200300",
            "raw_payload": {"id": "900100200300", "page": None},
        }
        self.capture([ad], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            row = conn.execute(
                "SELECT page_source_id, page_name, creative_text, start_date, status, product_id FROM facebook_ads"
            ).fetchone()
            self.assertIsNone(row["page_source_id"])
            self.assertIsNone(row["page_name"])
            self.assertIsNone(row["creative_text"])
            self.assertIsNone(row["start_date"])
            self.assertEqual(row["status"], "unknown")
            self.assertIsNone(row["product_id"])
            observation = conn.execute(
                "SELECT active, detected_metadata_json FROM facebook_ad_observations"
            ).fetchone()
            self.assertIsNone(observation["active"])
            payload = json.loads(observation["detected_metadata_json"])
            self.assertIsNone(payload["page_name"])
            self.assertIsNone(payload["creative_text"])
            self.assertIsNone(payload["start_date"])

    def test_changed_creative_appends_observation_without_mutating_history(self):
        self.capture([source_ad(creative_text="Original ad")], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            original = dict(conn.execute("SELECT * FROM facebook_ad_observations").fetchone())
        self.capture([source_ad(creative_text="Changed ad")], "2026-10-06T11:00:00Z")
        with self.opened_database() as conn:
            rows = conn.execute(
                "SELECT * FROM facebook_ad_observations ORDER BY observed_at"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(dict(rows[0]), original)
            self.assertEqual(
                json.loads(rows[1]["detected_metadata_json"])["creative_text"],
                "Changed ad",
            )
            self.assertNotEqual(rows[0]["creative_fingerprint"], rows[1]["creative_fingerprint"])

    def test_sensor_never_creates_products_or_links_an_ad(self):
        self.capture([source_ad()], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT product_id FROM facebook_ads").fetchone()[0])

    def test_observation_failure_rolls_back_entire_capture(self):
        with self.opened_database() as conn:
            conn.execute(
                """CREATE TRIGGER fail_observation BEFORE INSERT ON facebook_ad_observations
                   BEGIN SELECT RAISE(ABORT, 'forced observation failure'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.capture([source_ad()], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 0)

    def test_duplicate_input_rows_produce_only_one_ad_and_observation(self):
        ad = source_ad()
        stats = self.capture([ad, dict(ad)], "2026-10-06T10:00:00Z")
        self.assertEqual((stats.discovered, stats.unique_ads, stats.duplicate_inputs), (2, 1, 1))
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 1)

    def test_status_is_only_set_from_explicit_active_or_inactive_label(self):
        ads = [
            source_ad(library_id="1", status="Active"),
            source_ad(library_id="2", status="Inactive"),
            source_ad(library_id="3", status=None),
        ]
        self.capture(ads, "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            rows = conn.execute(
                "SELECT library_id, status FROM facebook_ads ORDER BY library_id"
            ).fetchall()
            self.assertEqual([(r["library_id"], r["status"]) for r in rows], [
                ("1", "active"), ("2", "inactive"), ("3", "unknown")
            ])
            observation_states = conn.execute(
                "SELECT active FROM facebook_ad_observations ORDER BY ad_id"
            ).fetchall()
            self.assertEqual([row[0] for row in observation_states], [1, 0, None])
        self.assertEqual(sensor.normalize_status("not active"), "inactive")
        self.assertEqual(sensor.normalize_status("Active creative message"), "unknown")

    def test_raw_source_payload_and_query_provenance_are_preserved(self):
        original = {"libraryId": "id-in-source", "media": ["opaque-value"]}
        self.capture(
            [source_ad(raw_payload=original)],
            "2026-10-06T10:00:00Z",
            keyword="Peinture",
            country="DZ",
        )
        with self.opened_database() as conn:
            ad_payload = json.loads(conn.execute("SELECT source_payload_json FROM facebook_ads").fetchone()[0])
            observation = json.loads(
                conn.execute("SELECT detected_metadata_json FROM facebook_ad_observations").fetchone()[0]
            )
            self.assertEqual(ad_payload["raw_source_payload"], original)
            self.assertEqual(observation["raw_source_payload"], original)
            self.assertEqual(observation["capture_query"], {"keyword": "Peinture", "country": "DZ"})
            self.assertEqual(observation["identity"]["method"], "facebook_library_id")

    def test_legacy_facebook_table_is_not_written(self):
        self.capture([source_ad()], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            legacy = conn.execute("SELECT note FROM legacy_advertising_campaigns WHERE id = 1").fetchone()[0]
            self.assertEqual(legacy, "preserve this row")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM legacy_advertising_campaigns").fetchone()[0], 1)

    def test_same_timestamp_integrity_error_is_not_silently_swallowed(self):
        self.capture([source_ad()], "2026-10-06T10:00:00Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self.capture([source_ad()], "2026-10-06T10:00:00Z")
        with self.opened_database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ads").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 1)

    def test_timestamp_requires_timezone(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            sensor.store_capture([source_ad()], "2026-10-06T10:00:00", db_connect=self._connect)

    def test_status_and_start_date_parsing_are_conservative(self):
        self.assertEqual(sensor.parse_start_date("Oct 2, 2026"), "2026-10-02")
        self.assertIsNone(sensor.parse_start_date("2 أكتوبر 2026"))
        self.assertEqual(sensor.normalize_status("Inactive"), "inactive")
        self.assertEqual(sensor.normalize_status(None), "unknown")

    def test_dom_card_extraction_keeps_advertiser_creative_media_and_destination(self):
        card = {
            "text": (
                "Active\nLibrary ID: 888039994142371\nStarted running on Feb 13, 2026\n"
                "Platforms\nSIB auto electronic diagnostic\nSponsored\nscanner auto diagnostics"
            ),
            "links": [
                {"href": "https://web.facebook.com/SibAutoDZ/", "text": "SIB auto electronic diagnostic"},
                {"href": "https://www.facebook.com/profile.php?id=112015387295767", "text": "SIB auto electronic diagnostic Auto Service"},
                {
                    "href": "https://l.facebook.com/l.php?u=https%3A%2F%2Fshop.example.test%2Fitem&h=opaque",
                    "text": "Open item Learn More",
                },
            ],
            "buttons": [
                {"text": "Open Dropdown"},
                {"text": "See ad details"},
                {"text": "scanner auto diagnostics"},
                {"text": "Learn More"},
            ],
            "images": [{"src": "https://cdn.example.test/ad.jpg", "alt": "SIB auto electronic diagnostic"}],
            "videos": [{"src": "https://cdn.example.test/ad.mp4", "poster": None}],
            "platform_labels": [],
            "attributes": {},
        }

        extracted = sensor._extract_card(card)
        self.assertEqual(extracted["library_id"], "888039994142371")
        self.assertEqual(extracted["source_status"], "Active")
        self.assertEqual(extracted["start_date_text"], "Feb 13, 2026")
        self.assertEqual(extracted["page_name"], "SIB auto electronic diagnostic")
        self.assertEqual(extracted["page_source_id"], "112015387295767")
        self.assertEqual(extracted["creative_text"], "scanner auto diagnostics")
        self.assertEqual(extracted["image_urls"], ["https://cdn.example.test/ad.jpg"])
        self.assertEqual(extracted["video_urls"], ["https://cdn.example.test/ad.mp4"])
        self.assertEqual(extracted["landing_url"], "https://shop.example.test/item")
        self.assertEqual(extracted["platforms"], [])

    def test_captured_open_dropdown_variant_does_not_replace_real_card_copy(self):
        card = captured_facebook_card_fixture()
        original_card = copy.deepcopy(card)

        extracted = sensor._extract_card(card)

        self.assertEqual(extracted["creative_text"], card["buttons"][2]["text"])
        self.assertNotEqual(extracted["creative_text"], card["buttons"][0]["text"])
        self.assertEqual(extracted["page_name"], "QuickNest")
        self.assertEqual(card, original_card)

    def test_open_dropdown_control_variants_are_not_creative_text(self):
        variants = (
            "Open Dropdown",
            "Open Dropdown\u200b",
            "Open\u200b Dropdown\u200b",
            " \tOpen\u200b  Dropdown\r\n\u200b ",
        )
        for variant in variants:
            with self.subTest(variant=repr(variant)):
                card = captured_facebook_card_fixture()
                card["text"] = (
                    "Library ID: 2617245195313634\nSan Marco\nSponsored\n" + variant
                )
                card["buttons"] = [{"text": variant, "aria_label": None}]

                extracted = sensor._extract_card(card)

                self.assertIsNone(extracted["creative_text"])
                normalized = sensor.normalize_ad(extracted)
                self.assertIsNone(normalized["creative_text"])

    def test_missing_creative_page_name_and_arbitrary_button_labels_stay_null(self):
        card = captured_facebook_card_fixture()
        card["text"] = "Library ID: 1834446447534350\nQuickNest\nSponsored\n"
        card["buttons"] = [
            {"text": "Unrelated button label", "aria_label": None},
            {"text": "QuickNest", "aria_label": None},
        ]

        extracted = sensor._extract_card(card)

        self.assertEqual(extracted["page_name"], "QuickNest")
        self.assertIsNone(extracted["creative_text"])

    def test_extracted_observation_keeps_raw_card_evidence_unchanged(self):
        card = captured_facebook_card_fixture()
        original_card = copy.deepcopy(card)
        expected_raw = {
            "card_text": original_card["text"],
            "links": original_card["links"],
            "buttons": original_card["buttons"],
            "images": original_card["images"],
            "videos": original_card["videos"],
            "attributes": original_card["attributes"],
        }
        extracted = sensor._extract_card(card)
        self.assertEqual(extracted["raw_payload"], expected_raw)

        self.capture([extracted], "2026-10-06T10:00:00Z")

        with self.opened_database() as conn:
            row = conn.execute(
                "SELECT detected_metadata_json FROM facebook_ad_observations"
            ).fetchone()
            payload = json.loads(row["detected_metadata_json"])
            self.assertEqual(payload["raw_source_payload"], expected_raw)
            self.assertEqual(payload["creative_text"], extracted["creative_text"])
        self.assertEqual(card, original_card)


class FakeBrowserLocator:
    def __init__(self, page):
        self.page = page

    def wait_for(self, *, state, timeout):
        self.page.events.append(("body_wait", state))


class FakeBrowserPage:
    def __init__(self, *, evaluation_error=None, navigation_on_first_evaluation=False):
        self.events = []
        self.navigation_pending = True
        self.evaluation_error = evaluation_error
        self.navigation_on_first_evaluation = navigation_on_first_evaluation
        self.evaluations = 0

    def wait_for_load_state(self, state, *, timeout):
        self.events.append(("load_wait", state))
        self.navigation_pending = False

    def locator(self, selector):
        self.events.append(("locator", selector))
        return FakeBrowserLocator(self)

    def wait_for_function(self, expression, **kwargs):
        kind = "results_wait" if expression == sensor.INITIAL_RESULTS_SCRIPT else "stable_wait"
        self.events.append((kind, kwargs))

    def evaluate(self, expression):
        self.events.append(("evaluate", expression))
        self.evaluations += 1
        if self.navigation_pending:
            raise AssertionError("evaluate called while navigation was still pending")
        if self.navigation_on_first_evaluation and self.evaluations == 1:
            self.navigation_pending = True
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation.")
        if self.evaluation_error is not None:
            raise self.evaluation_error
        return {"cards": [], "controls": [], "body": ""}


class FacebookBrowserLifecycleTests(unittest.TestCase):
    def test_waits_for_document_and_stable_dom_before_evaluating(self):
        page = FakeBrowserPage()

        result = sensor._read_stable_dom(page, wait_for_results=True)

        self.assertEqual(result["cards"], [])
        event_names = [event[0] for event in page.events]
        self.assertEqual(
            event_names,
            ["load_wait", "locator", "body_wait", "results_wait", "stable_wait", "evaluate"],
        )
        self.assertEqual(page.events[0], ("load_wait", "domcontentloaded"))
        self.assertFalse(page.navigation_pending)

    def test_navigation_during_snapshot_waits_for_new_document_then_recovers(self):
        page = FakeBrowserPage(navigation_on_first_evaluation=True)

        result = sensor._read_stable_dom(page)

        self.assertEqual(result["cards"], [])
        self.assertEqual(page.evaluations, 2)
        self.assertEqual([event[0] for event in page.events].count("load_wait"), 2)
        self.assertFalse(page.navigation_pending)

    def test_unrecoverable_browser_error_is_propagated_without_retry(self):
        error = RuntimeError("browser disconnected")
        page = FakeBrowserPage(evaluation_error=error)

        with self.assertRaisesRegex(RuntimeError, "browser disconnected"):
            sensor._read_stable_dom(page)

        self.assertEqual(page.evaluations, 1)

    def test_browser_collection_failure_never_calls_store_capture(self):
        with patch.object(sensor, "collect_live_ads", side_effect=RuntimeError("browser failed")):
            with patch.object(sensor, "store_capture") as store_capture:
                with self.assertRaisesRegex(RuntimeError, "browser failed"):
                    sensor.run_live_capture(keyword="Peinture", country="DZ")

        store_capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
