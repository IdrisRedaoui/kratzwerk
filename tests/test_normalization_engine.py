from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from normalization.engine import NormalizationEngine
from normalization.persistence import load_source_observation
from normalization.extract import (
    RecordClass,
    classify_record,
    extract_product_mentions,
    select_facebook_text,
    select_ouedkniss_text,
)
from normalization.text import DEFAULT_PROFILE, normalize_text
from normalization.versioning import (
    DEFAULT_VERSIONS,
    EngineVersions,
    new_classification_version,
    new_text_version,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_SCHEMA = ROOT / "tests" / "fixtures" / "normalization_base.sql"
NORMALIZATION_SCHEMA = ROOT / "normalization" / "schema_v1.sql"


class NormalizationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kratzwerk-engine-")
        self.db_path = Path(self.temp.name) / "engine-test.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(BASE_SCHEMA.read_text(encoding="utf-8"))
        self.conn.executescript(NORMALIZATION_SCHEMA.read_text(encoding="utf-8"))
        self.conn.commit()
        self.engine = NormalizationEngine()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def add_facebook(self, ad_id: int, observation_id: int, card_text: str | None,
                     *, page_name: str = "Page Shop",
                     creative_text: str = "Open Dropdown\n\u200b",
                     observation_page_name: str | None = "Page Shop") -> None:
        self.conn.execute(
            "INSERT INTO facebook_ads(id, platform, page_name, creative_text) "
            "VALUES (?, 'facebook', ?, ?)", (ad_id, page_name, creative_text),
        )
        metadata = {"creative_text": creative_text}
        if observation_page_name is not None:
            metadata["page_name"] = observation_page_name
        if card_text is not None:
            metadata["raw_source_payload"] = {"card_text": card_text}
        self.conn.execute(
            """INSERT INTO facebook_ad_observations
               (id, ad_id, observed_at, detected_metadata_json)
               VALUES (?, ?, ?, ?)""",
            (observation_id, ad_id, f"2026-10-07T00:00:{observation_id % 60:02d}Z",
             json.dumps(metadata, ensure_ascii=False)),
        )

    def add_ouedkniss(self, listing_id: int, observation_id: int,
                      entity_title: str, observed_title: str | None,
                      **announcement_fields: object) -> None:
        self.conn.execute(
            "INSERT INTO marketplace_listings(id, platform, raw_title) VALUES (?, 'Ouedkniss', ?)",
            (listing_id, entity_title),
        )
        announcement: dict[str, object] = dict(announcement_fields)
        if observed_title is not None:
            announcement["raw_title"] = observed_title
        payload = {"announcement": announcement, "seller": {"display_name": "Never product evidence"}}
        self.conn.execute(
            """INSERT INTO marketplace_listing_observations
               (id, listing_id, observed_at, status, source_payload_json)
               VALUES (?, ?, ?, 'active', ?)""",
            (observation_id, listing_id, f"2026-10-07T01:00:{observation_id % 60:02d}Z",
             json.dumps(payload, ensure_ascii=False)),
        )

    def add_product(self, product_id: int, name: str, *, category: str | None = None,
                    attributes: dict[str, object] | None = None) -> None:
        self.conn.execute(
            """INSERT INTO products(id, canonical_name, category, attributes_json)
               VALUES (?, ?, ?, ?)""",
            (product_id, name, category, json.dumps(attributes or {}, ensure_ascii=False)),
        )

    def add_alias(self, product_id: int, alias: str, *, source_type: str = "facebook") -> int:
        normalized_alias = normalize_text(alias).normalized_text
        cursor = self.conn.execute(
            """INSERT INTO product_aliases
               (product_id, source_type, raw_alias, normalized_alias)
               VALUES (?, ?, ?, ?)""",
            (product_id, source_type, alias, normalized_alias),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def facebook_card(copy: str) -> str:
        return (
            "Inactive\nLibrary ID: 123\nPlatforms\nOpen Dropdown\nSee ad details\n"
            "Page Shop\nSponsored \u2022 Paid for by Example Page\n"
            f"{copy}\nSHOP NOW\nLearn More"
        )

    def table_count(self, table: str) -> int:
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_01_unicode_compatibility_normalization(self) -> None:
        result = normalize_text("ＰＥＩＮＴＵＲＥ  ÉCOLE")
        self.assertEqual(result.normalized_text, "peinture école")

    def test_02_zero_width_characters_are_removed(self) -> None:
        self.assertEqual(normalize_text("Com\u200bpressor").normalized_text, "compressor")

    def test_03_control_characters_are_removed_or_spaced(self) -> None:
        self.assertEqual(normalize_text("ABC\x00-123\x01").normalized_text, "abc-123")
        self.assertEqual(normalize_text("line\nbreak").normalized_text, "line break")

    def test_04_unicode_and_repeated_whitespace_are_collapsed(self) -> None:
        self.assertEqual(normalize_text("  A\u00a0\tB\n\nC  ").normalized_text, "a b c")

    def test_05_model_identifiers_are_preserved(self) -> None:
        self.assertEqual(normalize_text("ABC-123 ATX/9000").normalized_text, "abc-123 atx/9000")

    def test_06_capacity_and_specification_tokens_remain_distinguishable(self) -> None:
        value = normalize_text("9L 24L 500ml 1.5L 220V 2026 ABC-123").normalized_text
        self.assertEqual(value, "9l 24l 500ml 1.5l 220v 2026 abc-123")
        self.assertEqual(len(set(value.split())), 7)

    def test_07_language_detection_is_conservative(self) -> None:
        self.assertEqual(normalize_text("La peinture blanche pour les murs").language_code, "fr")
        self.assertEqual(normalize_text("المنتج الجديد متوفر للبيع").language_code, "ar")
        self.assertIsNone(normalize_text("Compressor 9L").language_code)

    def test_behavior_changes_have_explicit_non_v1_versions(self) -> None:
        self.assertEqual(DEFAULT_VERSIONS.selector_version, "source_text_selector_v2")
        self.assertEqual(DEFAULT_VERSIONS.extraction_version, "deterministic_extract_v2")
        self.assertEqual(DEFAULT_VERSIONS.classification_version, "classification_v2")
        self.assertEqual(DEFAULT_VERSIONS.text_profile.version, "deterministic_v1")
        self.assertEqual(DEFAULT_VERSIONS.matching_version, "deterministic_match_v1")

    def test_08_product_record_classification(self) -> None:
        self.assertEqual(classify_record("Peinture blanche 2L").value, RecordClass.PRODUCT)

    def test_09_service_record_classification(self) -> None:
        self.assertEqual(classify_record("Location de voiture avec chauffeur").value,
                         RecordClass.SERVICE)

    def test_10_real_estate_record_classification(self) -> None:
        self.assertEqual(classify_record("Vente Appartement F3 Boumerdès").value,
                         RecordClass.REAL_ESTATE)

    def test_11_education_record_classification(self) -> None:
        self.assertEqual(classify_record("Summer camp for school students").value,
                         RecordClass.EDUCATION)

    def test_12_unknown_record_classification(self) -> None:
        result = classify_record("Welcome to our page")
        self.assertEqual(result.value, RecordClass.UNKNOWN)
        self.assertEqual(result.rule_id, "no_supported_classification_v1")

    def test_13_facebook_product_ad_uses_raw_card_copy_not_ui_or_page_name(self) -> None:
        card = self.facebook_card("Compressor 9L ABC-123")
        selection = select_facebook_text(
            {"creative_text": "Open Dropdown\n\u200b",
             "page_name": "Page Shop",
             "raw_source_payload": {"card_text": card}},
        )
        self.assertEqual(selection.raw_text, "Compressor 9L ABC-123")
        self.assertNotIn("Open Dropdown", selection.raw_text)
        self.assertNotIn("Page Shop", selection.raw_text)
        self.assertEqual(selection.field_path,
                         "facebook_ad_observations.detected_metadata_json.raw_source_payload.card_text")
        self.assertEqual(card[selection.source_span_start:selection.source_span_end], selection.raw_text)

    def test_facebook_observation_local_page_name_is_used(self) -> None:
        card = "Sponsored\nObservation Page\nPeinture blanche 2L\nShop Now"
        self.add_facebook(701, 701, card, page_name="Current Entity Page",
                         observation_page_name="Observation Page")
        loaded = load_source_observation(self.conn, "facebook_ad", 701)
        self.assertEqual(loaded.raw_text, "Peinture blanche 2L")

    def test_facebook_current_page_name_is_ignored_when_observation_has_its_own(self) -> None:
        card = "Sponsored\nObserved Page\nCompressor 9L\nShop Now"
        self.add_facebook(702, 702, card, page_name="Mutable Current Page",
                         observation_page_name="Observed Page")
        loaded = load_source_observation(self.conn, "facebook_ad", 702)
        self.assertEqual(loaded.raw_text, "Compressor 9L")

    def test_facebook_page_name_absence_does_not_use_entity_fallback(self) -> None:
        copy = "Peinture blanche 2L"
        self.add_facebook(703, 703, f"Sponsored\n{copy}", page_name=copy,
                         observation_page_name=None)
        loaded = load_source_observation(self.conn, "facebook_ad", 703)
        self.assertEqual(loaded.raw_text, copy)

    def test_facebook_entity_payload_is_ignored_when_observation_payload_is_absent(self) -> None:
        copy = "Sponsored\nCompressor 9L"
        self.add_facebook(704, 704, None)
        self.conn.execute(
            "UPDATE facebook_ads SET source_payload_json=? WHERE id=?",
            (json.dumps({"raw_source_payload": {"card_text": copy}}), 704),
        )
        loaded = load_source_observation(self.conn, "facebook_ad", 704)
        self.assertEqual(loaded.raw_text, "")

    def test_later_entity_payload_cannot_backfill_an_earlier_observation(self) -> None:
        self.conn.execute(
            """INSERT INTO facebook_ads
               (id, platform, page_name, creative_text, source_payload_json)
               VALUES (705, 'facebook', 'Later Page', 'Later creative', '{}')"""
        )
        self.conn.execute(
            """INSERT INTO facebook_ad_observations
               (id, ad_id, observed_at, detected_metadata_json)
               VALUES (705, 705, '2026-10-01T00:00:00Z', '{}')"""
        )
        later_card = "Sponsored\nLater copy Compressor 9L"
        self.conn.execute(
            """INSERT INTO facebook_ad_observations
               (id, ad_id, observed_at, detected_metadata_json)
               VALUES (706, 705, '2026-10-02T00:00:00Z', ?)""",
            (json.dumps({"raw_source_payload": {"card_text": later_card}}),),
        )
        self.conn.execute(
            """UPDATE facebook_ads SET source_payload_json=?, creative_text=?
               WHERE id=705""",
            (json.dumps({"raw_source_payload": {"card_text": later_card}}), "Later copy Compressor 9L"),
        )
        earlier = load_source_observation(self.conn, "facebook_ad", 705)
        later = load_source_observation(self.conn, "facebook_ad", 706)
        self.assertEqual(earlier.raw_text, "")
        self.assertEqual(later.raw_text, "Later copy Compressor 9L")

    def test_reprocessing_is_stable_when_current_entity_fields_change(self) -> None:
        self.add_facebook(707, 707, "Sponsored\nObserved Page\nCompressor 9L",
                         page_name="Initial Entity Page",
                         observation_page_name="Observed Page")
        first = self.engine.process_observation(self.conn, "facebook_ad", 707)
        first_run = tuple(self.conn.execute(
            """SELECT normalization_version, selector_version, input_field_path,
                      selected_input_text, input_hash, normalized_text
               FROM normalization_runs WHERE id=?""", (first.normalization_run_id,),
        ).fetchone())
        self.conn.execute(
            """UPDATE facebook_ads
               SET page_name='Changed entity page', creative_text='Changed entity copy',
                   start_date='2099-01-01', source_payload_json='{"later":"payload"}'
               WHERE id=707"""
        )
        second = self.engine.process_observation(self.conn, "facebook_ad", 707)
        second_run = tuple(self.conn.execute(
            """SELECT normalization_version, selector_version, input_field_path,
                      selected_input_text, input_hash, normalized_text
               FROM normalization_runs WHERE id=?""", (second.normalization_run_id,),
        ).fetchone())
        self.assertEqual(second.normalization_run_id, first.normalization_run_id)
        self.assertEqual(second.classification, first.classification)
        self.assertEqual(second_run, first_run)

    def test_14_facebook_service_ad_is_selected_from_observation(self) -> None:
        text = "Location de voiture avec chauffeur"
        self.add_facebook(14, 14, self.facebook_card(text))
        result = self.engine.process_observation(self.conn, "facebook_ad", 14)
        self.assertEqual(result.classification.value, RecordClass.SERVICE)
        self.assertEqual(result.mention_ids, ())

    def test_15_facebook_real_estate_ad_is_not_a_product_mention(self) -> None:
        self.add_facebook(15, 15, self.facebook_card("Vente Appartement F3 Boumerdès"))
        result = self.engine.process_observation(self.conn, "facebook_ad", 15)
        self.assertEqual(result.classification.value, RecordClass.REAL_ESTATE)
        self.assertEqual(result.mention_ids, ())

    def test_16_facebook_education_ad_is_not_a_product_mention(self) -> None:
        self.add_facebook(16, 16, self.facebook_card("Summer camp for school students"))
        result = self.engine.process_observation(self.conn, "facebook_ad", 16)
        self.assertEqual(result.classification.value, RecordClass.EDUCATION)
        self.assertEqual(result.mention_ids, ())

    def test_17_facebook_ad_with_two_explicit_items_produces_two_mentions(self) -> None:
        self.add_facebook(17, 17, self.facebook_card("Compressor 9L + Accessory kit"))
        result = self.engine.process_observation(self.conn, "facebook_ad", 17)
        self.assertEqual(result.classification.value, RecordClass.PRODUCT)
        rows = self.conn.execute(
            "SELECT source_text_span, mention_kind FROM product_mentions ORDER BY mention_index"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("Compressor 9L", "product"), ("Accessory kit", "accessory"),
        ])

    def test_18_ouedkniss_uses_observation_title_and_structured_brand(self) -> None:
        self.add_ouedkniss(
            18, 18, "mutable listing title", "Compresseur 9L",
            brand="Makita", model="ABC-123",
        )
        result = self.engine.process_observation(self.conn, "marketplace_listing", 18)
        self.assertEqual(result.classification.value, RecordClass.PRODUCT)
        row = self.conn.execute(
            "SELECT source_text_span, brand, model, capacity_value, capacity_unit "
            "FROM product_mentions WHERE id=?", (result.mention_ids[0],),
        ).fetchone()
        self.assertEqual(tuple(row), ("Compresseur 9L\nMakita\nABC-123", "Makita", "ABC-123", 9.0, "l"))
        path = self.conn.execute(
            "SELECT input_field_path FROM normalization_runs WHERE id=?",
            (result.normalization_run_id,),
        ).fetchone()[0]
        self.assertIn("marketplace_listing_observations.source_payload_json", path)
        self.assertNotIn("mutable listing title", row[0])

    def test_marketplace_entity_title_is_ignored_when_observation_title_differs(self) -> None:
        self.add_ouedkniss(180, 180, "mutable current title", "Peinture ACRYLIQUE Satiné")
        loaded = load_source_observation(self.conn, "marketplace_listing", 180)
        self.assertEqual(loaded.raw_text, "Peinture ACRYLIQUE Satiné")
        self.assertIn("marketplace_listing_observations.source_payload_json", loaded.field_path)
        self.assertNotIn("marketplace_listings.raw_title", loaded.field_path)

    def test_marketplace_missing_observation_title_has_no_entity_fallback(self) -> None:
        self.add_ouedkniss(181, 181, "mutable current title", None)
        loaded = load_source_observation(self.conn, "marketplace_listing", 181)
        self.assertEqual(loaded.raw_text, "")
        self.assertEqual(
            loaded.field_path,
            "marketplace_listing_observations.source_payload_json.announcement.raw_title#missing",
        )
        result = self.engine.process_observation(self.conn, "marketplace_listing", 181)
        self.assertEqual(result.classification.value, RecordClass.UNKNOWN)
        self.assertEqual(result.mention_ids, ())

    def test_later_entity_title_cannot_backfill_an_earlier_marketplace_observation(self) -> None:
        self.add_ouedkniss(182, 182, "Initial entity title", None)
        self.conn.execute(
            "UPDATE marketplace_listings SET raw_title=? WHERE id=?",
            ("Later entity title Peinture ACRYLIQUE", 182),
        )
        loaded = load_source_observation(self.conn, "marketplace_listing", 182)
        self.assertEqual(loaded.raw_text, "")
        self.assertNotIn("Later entity title", loaded.raw_text)

    def test_marketplace_entity_title_mutation_does_not_change_historical_normalization(self) -> None:
        self.add_ouedkniss(183, 183, "Initial entity title", None)
        first = self.engine.process_observation(self.conn, "marketplace_listing", 183)
        first_run = tuple(self.conn.execute(
            """SELECT normalization_version, selector_version, input_field_path,
                      selected_input_text, input_hash, normalized_text
               FROM normalization_runs WHERE id=?""", (first.normalization_run_id,),
        ).fetchone())
        self.conn.execute(
            "UPDATE marketplace_listings SET raw_title=? WHERE id=?",
            ("Later entity title Peinture ACRYLIQUE", 183),
        )
        second = self.engine.process_observation(self.conn, "marketplace_listing", 183)
        second_run = tuple(self.conn.execute(
            """SELECT normalization_version, selector_version, input_field_path,
                      selected_input_text, input_hash, normalized_text
               FROM normalization_runs WHERE id=?""", (second.normalization_run_id,),
        ).fetchone())
        self.assertEqual(second.normalization_run_id, first.normalization_run_id)
        self.assertEqual(second.classification, first.classification)
        self.assertEqual(second_run, first_run)

    def test_19_missing_text_becomes_idempotent_zero_mention_result(self) -> None:
        self.add_facebook(19, 19, None)
        first = self.engine.process_observation(self.conn, "facebook_ad", 19)
        second = self.engine.process_observation(self.conn, "facebook_ad", 19)
        self.assertEqual(first.classification.value, RecordClass.UNKNOWN)
        self.assertEqual(first.mention_ids, ())
        self.assertEqual(first.normalization_run_id, second.normalization_run_id)
        self.assertEqual(self.table_count("product_mentions"), 0)
        self.assertEqual(self.table_count("normalization_runs"), 1)

    def test_20_facebook_control_only_payload_is_not_used_as_copy(self) -> None:
        self.add_facebook(20, 20, "Open Dropdown\n\u200b\nSee ad details\nLearn More")
        result = self.engine.process_observation(self.conn, "facebook_ad", 20)
        self.assertEqual(result.classification.value, RecordClass.UNKNOWN)
        self.assertEqual(result.mention_ids, ())

    def test_21_unsupported_mention_attributes_remain_null(self) -> None:
        classification, mentions = extract_product_mentions(
            "Compressor 9L", profile=DEFAULT_PROFILE,
        )
        self.assertEqual(classification.value, RecordClass.PRODUCT)
        mention = mentions[0]
        self.assertEqual(mention.category, "compressor")
        self.assertEqual(mention.capacity_value, 9.0)
        self.assertIsNone(mention.brand)
        self.assertIsNone(mention.model)
        self.assertIsNone(mention.variant)
        self.assertIsNone(mention.quantity_value)

    def test_arabic_crown_paint_sprayer_emits_product_mention_with_evidence(self) -> None:
        text = "مسدس رش الدهان الكهربائي CROWN CT31012 – 80 واط"
        classification, mentions = extract_product_mentions(text, profile=DEFAULT_PROFILE)
        self.assertEqual(classification.value, RecordClass.PRODUCT)
        self.assertIn("مسدس رش الدهان الكهربائي", classification.matched_terms)
        self.assertEqual(len(mentions), 1)
        mention = mentions[0]
        self.assertEqual((mention.brand, mention.model, mention.category), (
            "CROWN", "CT31012", "power_tool",
        ))
        self.assertEqual(json.loads(mention.specifications_json or "{}"), {
            "values": [{"value": "80", "unit": "واط"}],
        })
        self.assertEqual(mention.extraction_method, "deterministic_rules_v2")

    def test_unrelated_arabic_text_and_unscoped_crown_do_not_become_products(self) -> None:
        for text in ("مرحبا بكم في صفحتنا", "CROWN CT31012", "CROWN في إعلان جديد"):
            with self.subTest(text=text):
                classification, mentions = extract_product_mentions(
                    text, profile=DEFAULT_PROFILE,
                )
                self.assertNotEqual(classification.value, RecordClass.PRODUCT)
                self.assertEqual(mentions, ())
        classification, mentions = extract_product_mentions(
            "Compressor CROWN CT31012", profile=DEFAULT_PROFILE,
        )
        self.assertEqual(classification.value, RecordClass.PRODUCT)
        self.assertIsNone(mentions[0].brand)
        self.assertIsNone(mentions[0].model)

    def test_paint_art_inspiration_work_and_career_contexts_are_not_products(self) -> None:
        texts = (
            "peinture traditionnelle chinoise",
            "idées de peinture modernes au-delà du beige",
            "peinture à l'encre",
            "ponçage, peinture, vernis",
            "La peinture offre expression créative, flexibilité et potentiel d'évolution. "
            "Apprenez à démarrer votre carrière et à faire de vos compétences votre métier.",
        )
        for text in texts:
            with self.subTest(text=text):
                classification, mentions = extract_product_mentions(
                    text, profile=DEFAULT_PROFILE,
                )
                self.assertNotEqual(classification.value, RecordClass.PRODUCT)
                self.assertEqual(mentions, ())

    def test_supported_paint_product_and_numbered_kit_remain_products(self) -> None:
        valid_product_texts = (
            "Peinture ACRYLIQUE Satiné",
            "20 minutes de peinture réduisent le stress. Des fleurs délicates aux tons pastel, "
            "chaque zone numérotée, chaque couleur pré-mélangée.",
            "Un kit de peinture par numéros bas de gamme peut ruiner le plaisir. Davincified règle ça.",
        )
        for text in valid_product_texts:
            with self.subTest(text=text):
                classification, mentions = extract_product_mentions(
                    text, profile=DEFAULT_PROFILE,
                )
                self.assertEqual(classification.value, RecordClass.PRODUCT)
                self.assertEqual(len(mentions), 1)

    def test_22_explicit_brand_model_quantity_variant_and_specs_are_extracted(self) -> None:
        _classification, mentions = extract_product_mentions(
            "Brand: Makita Model: ABC-123 Compressor 9L 2 pcs Variant: red 220V",
            profile=DEFAULT_PROFILE,
        )
        mention = mentions[0]
        self.assertEqual(mention.brand, "Makita")
        self.assertEqual(mention.model, "ABC-123")
        self.assertEqual(mention.category, "compressor")
        self.assertEqual(mention.variant, "red")
        self.assertEqual((mention.quantity_value, mention.quantity_unit), (2.0, "pcs"))
        self.assertEqual((mention.capacity_value, mention.capacity_unit), (9.0, "l"))
        self.assertIn("220", mention.specifications_json or "")

    def test_23_one_mention_with_no_products_stays_unmapped(self) -> None:
        self.add_facebook(23, 23, self.facebook_card("Compressor 9L"))
        result = self.engine.process_observation(self.conn, "facebook_ad", 23)
        self.assertEqual(result.candidate_count, 0)
        event = self.conn.execute(
            "SELECT decision_state, confidence_label FROM normalization_decision_events"
        ).fetchone()
        self.assertEqual(tuple(event), ("UNMAPPED", "NO_MATCH"))

    def test_24_exact_unique_alias_candidate_matches_high(self) -> None:
        phrase = "Compressor Model ABC-123"
        self.add_product(1, "Compressor ABC-123", attributes={"brand": "Bosch", "model": "ABC-123"})
        self.add_alias(1, phrase)
        self.add_facebook(24, 24, self.facebook_card(phrase))
        result = self.engine.process_observation(self.conn, "facebook_ad", 24)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.evidence_count, 1)
        decision = self.conn.execute(
            "SELECT decision_state, confidence_label, actor_type FROM normalization_decision_events"
        ).fetchone()
        self.assertEqual(tuple(decision), ("MATCHED", "HIGH", "rule"))
        confirmed = self.conn.execute(
            "SELECT product_id FROM confirmed_product_mentions"
        ).fetchone()
        self.assertEqual(confirmed[0], 1)

    def test_25_multiple_products_sharing_alias_require_review(self) -> None:
        phrase = "Compressor Model ABC-123"
        self.add_product(1, "Product A")
        self.add_product(2, "Product B")
        self.add_alias(1, phrase)
        self.add_alias(2, phrase)
        self.add_facebook(25, 25, self.facebook_card(phrase))
        result = self.engine.process_observation(self.conn, "facebook_ad", 25)
        self.assertEqual(result.candidate_count, 2)
        event = self.conn.execute(
            "SELECT decision_state, confidence_label FROM normalization_decision_events"
        ).fetchone()
        self.assertEqual(tuple(event), ("REVIEW", "UNCERTAIN"))
        self.assertEqual(self.table_count("confirmed_product_mentions"), 0)

    def test_26_brand_or_model_conflict_blocks_automatic_match(self) -> None:
        phrase = "Brand: Bosch Model: ABC-123 Compressor"
        self.add_product(1, "Conflicting product", attributes={"brand": "Makita", "model": "XYZ-900"})
        self.add_alias(1, phrase)
        self.add_facebook(26, 26, self.facebook_card(phrase))
        result = self.engine.process_observation(self.conn, "facebook_ad", 26)
        self.assertEqual(result.candidate_count, 1)
        event = self.conn.execute(
            "SELECT decision_state, reason FROM normalization_decision_events"
        ).fetchone()
        self.assertEqual(event["decision_state"], "REVIEW")
        self.assertIn("conflict", event["reason"])
        conflicts = self.conn.execute(
            "SELECT evidence_value FROM candidate_evidence WHERE evidence_type='deterministic_rule'"
        ).fetchall()
        self.assertEqual(len(conflicts), 2)

    def test_27_category_only_alias_is_review_not_automatic_match(self) -> None:
        self.add_product(1, "Generic compressor")
        self.add_alias(1, "Compressor")
        self.add_facebook(27, 27, self.facebook_card("Compressor"))
        result = self.engine.process_observation(self.conn, "facebook_ad", 27)
        self.assertEqual(result.candidate_count, 1)
        event = self.conn.execute(
            "SELECT decision_state, decision_method FROM normalization_decision_events"
        ).fetchone()
        self.assertEqual(tuple(event), ("REVIEW", "generic_alias_review_v1"))

    def test_28_evidence_explains_candidate_generation(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor ABC-123")
        alias_id = self.add_alias(1, phrase)
        self.add_facebook(28, 28, self.facebook_card(phrase))
        self.engine.process_observation(self.conn, "facebook_ad", 28)
        evidence = self.conn.execute(
            "SELECT evidence_type, evidence_value, evidence_source, product_alias_id, method_version "
            "FROM candidate_evidence"
        ).fetchone()
        self.assertEqual(tuple(evidence), (
            "exact_alias", phrase.casefold(), "product_aliases.normalized_alias",
            alias_id, DEFAULT_VERSIONS.matching_version,
        ))

    def test_29_same_observation_version_input_twice_creates_no_duplicates(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor")
        self.add_alias(1, phrase)
        self.add_facebook(29, 29, self.facebook_card(phrase))
        first = self.engine.process_observation(self.conn, "facebook_ad", 29)
        counts_before = {
            table: self.table_count(table) for table in (
                "normalization_source_refs", "normalization_runs",
                "normalization_extraction_runs", "classification_results",
                "product_mentions",
                "candidate_generation_runs", "product_candidates",
                "candidate_evidence", "normalization_decision_events",
            )
        }
        second = self.engine.process_observation(self.conn, "facebook_ad", 29)
        counts_after = {table: self.table_count(table) for table in counts_before}
        self.assertEqual(counts_before, counts_after)
        self.assertEqual(first.normalization_run_id, second.normalization_run_id)
        self.assertEqual(first.extraction_run_id, second.extraction_run_id)
        self.assertEqual(first.classification_result_id, second.classification_result_id)
        self.assertEqual(second.decision_event_ids, ())

    def test_30_new_text_version_coexists_with_old_derived_result(self) -> None:
        self.add_facebook(30, 30, self.facebook_card("Compressor 9L"))
        old = self.engine.process_observation(self.conn, "facebook_ad", 30)
        next_engine = NormalizationEngine(new_text_version("deterministic_v2_test"))
        new = next_engine.process_observation(self.conn, "facebook_ad", 30)
        self.assertNotEqual(old.normalization_run_id, new.normalization_run_id)
        rows = self.conn.execute(
            "SELECT normalization_version, normalized_text FROM normalization_runs ORDER BY id"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("deterministic_v1", "compressor 9l"),
            ("deterministic_v2_test", "compressor 9l"),
        ])
        self.assertEqual(self.table_count("product_mentions"), 2)

    def test_31_decision_traces_to_exact_source_observation(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor")
        self.add_alias(1, phrase)
        self.add_facebook(31, 31, self.facebook_card(phrase))
        self.engine.process_observation(self.conn, "facebook_ad", 31)
        row = self.conn.execute(
            """SELECT r.source_platform, r.source_entity_type, r.source_record_id,
                      r.source_observation_id, o.id AS raw_observation_id,
                      n.normalization_version, x.extraction_version, m.source_text_span,
                      c.product_id, e.evidence_type, e.evidence_value,
                      d.decision_state, d.decision_method
               FROM normalization_decision_events d
               JOIN product_candidates c ON c.id=d.candidate_id
               JOIN candidate_evidence e ON e.candidate_id=c.id
               JOIN candidate_generation_runs g ON g.id=c.candidate_run_id
               JOIN product_mentions m ON m.id=g.product_mention_id
               JOIN normalization_extraction_runs x ON x.id=m.extraction_run_id
               JOIN normalization_runs n ON n.id=x.normalization_run_id
               JOIN normalization_source_refs r ON r.id=n.source_ref_id
               JOIN facebook_ad_observations o ON o.id=r.facebook_observation_id
               WHERE d.decision_state='MATCHED'"""
        ).fetchone()
        self.assertEqual(row["source_platform"], "facebook")
        self.assertEqual(row["source_entity_type"], "facebook_ad")
        self.assertEqual(row["source_record_id"], 31)
        self.assertEqual(row["source_observation_id"], 31)
        self.assertEqual(row["raw_observation_id"], 31)
        self.assertEqual(row["normalization_version"], "deterministic_v1")
        self.assertEqual(row["source_text_span"], phrase)
        self.assertEqual(row["evidence_type"], "exact_alias")
        self.assertEqual(row["decision_state"], "MATCHED")

    def test_32_same_numeric_source_ids_across_platforms_are_isolated(self) -> None:
        self.add_facebook(123, 123, self.facebook_card("Compressor 9L"))
        self.add_ouedkniss(123, 123, "mutable", "Peinture 2L")
        fb = self.engine.process_observation(self.conn, "facebook_ad", 123)
        oued = self.engine.process_observation(self.conn, "marketplace_listing", 123)
        self.assertNotEqual(fb.source_ref_id, oued.source_ref_id)
        rows = self.conn.execute(
            "SELECT source_platform, source_entity_type, source_record_id, source_observation_id "
            "FROM normalization_source_refs ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({(r["source_platform"], r["source_entity_type"],
                           r["source_record_id"], r["source_observation_id"]) for r in rows}, {
            ("facebook", "facebook_ad", 123, 123),
            ("Ouedkniss", "marketplace_listing", 123, 123),
        })
        classified = self.conn.execute(
            """SELECT r.source_platform, r.source_entity_type, r.source_observation_id,
                      c.classification_label
               FROM classification_results c
               JOIN normalization_source_refs r ON r.id=c.source_ref_id
               ORDER BY r.source_entity_type"""
        ).fetchall()
        self.assertEqual({(r["source_platform"], r["source_entity_type"],
                           r["source_observation_id"], r["classification_label"])
                          for r in classified}, {
            ("facebook", "facebook_ad", 123, "PRODUCT"),
            ("Ouedkniss", "marketplace_listing", 123, "PRODUCT"),
        })

    def test_33_raw_observations_and_catalog_tables_are_not_modified(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor")
        self.add_alias(1, phrase)
        self.add_facebook(33, 33, self.facebook_card(phrase))
        raw_before = tuple(self.conn.execute(
            "SELECT id, ad_id, observed_at, detected_metadata_json FROM facebook_ad_observations WHERE id=33"
        ).fetchone())
        counts_before = {
            table: self.table_count(table) for table in (
                "products", "product_aliases", "product_mappings",
                "marketplace_listing_observations", "facebook_ad_observations",
            )
        }
        self.engine.process_observation(self.conn, "facebook_ad", 33)
        raw_after = tuple(self.conn.execute(
            "SELECT id, ad_id, observed_at, detected_metadata_json FROM facebook_ad_observations WHERE id=33"
        ).fetchone())
        counts_after = {table: self.table_count(table) for table in counts_before}
        self.assertEqual(raw_before, raw_after)
        self.assertEqual(counts_before, counts_after)

    def test_34_failure_rolls_back_all_derived_rows(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor")
        self.add_alias(1, phrase)
        self.add_facebook(34, 34, self.facebook_card(phrase))
        self.conn.execute("""CREATE TRIGGER force_decision_failure
            BEFORE INSERT ON normalization_decision_events
            BEGIN SELECT RAISE(ABORT, 'injected decision failure'); END""")
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected decision failure"):
            self.engine.process_observation(self.conn, "facebook_ad", 34)
        for table in (
            "normalization_source_refs", "normalization_runs",
            "normalization_extraction_runs", "classification_results", "product_mentions",
            "candidate_generation_runs", "product_candidates", "candidate_evidence",
            "normalization_decision_events",
        ):
            self.assertEqual(self.table_count(table), 0, table)
        self.assertEqual(self.table_count("facebook_ad_observations"), 1)

    def _persisted_classification(self, source_id: int, text: str):
        self.add_facebook(source_id, source_id, self.facebook_card(text))
        result = self.engine.process_observation(self.conn, "facebook_ad", source_id)
        row = self.conn.execute(
            """SELECT c.*, r.source_platform, r.source_entity_type,
                      r.source_record_id, r.source_observation_id,
                      n.input_hash AS normalization_input_hash
               FROM classification_results c
               JOIN normalization_source_refs r ON r.id=c.source_ref_id
               JOIN normalization_runs n ON n.id=c.normalization_run_id
               WHERE c.id=?""",
            (result.classification_result_id,),
        ).fetchone()
        return result, row

    def test_38_product_classification_persists_with_actual_rule_evidence(self) -> None:
        result, row = self._persisted_classification(38, "Compressor 9L")
        self.assertEqual(row["classification_label"], "PRODUCT")
        self.assertEqual(row["classification_method"], "deterministic_rules")
        self.assertEqual(row["rule_id"], "explicit_product_terms_v2")
        self.assertEqual(row["classification_version"], "classification_v2")
        self.assertEqual(json.loads(row["evidence_json"]), {"matched_terms": ["compressor"]})
        self.assertEqual(row["normalization_input_hash"], row["input_hash"])
        self.assertEqual(result.classification_result_id, row["id"])

    def test_39_service_classification_persists(self) -> None:
        _result, row = self._persisted_classification(39, "Location de voiture")
        self.assertEqual(row["classification_label"], "SERVICE")
        self.assertEqual(row["rule_id"], "service_terms_v1")
        self.assertEqual(json.loads(row["evidence_json"]), {
            "matched_terms": ["location de voiture"],
        })

    def test_40_real_estate_classification_persists(self) -> None:
        _result, row = self._persisted_classification(40, "Vente Appartement F3")
        self.assertEqual(row["classification_label"], "REAL_ESTATE")
        self.assertEqual(row["rule_id"], "real_estate_terms_v1")
        self.assertEqual(json.loads(row["evidence_json"]), {
            "matched_terms": ["appartement", "vente appartement"],
        })

    def test_41_education_classification_persists(self) -> None:
        _result, row = self._persisted_classification(41, "Summer camp for school students")
        self.assertEqual(row["classification_label"], "EDUCATION")
        self.assertEqual(row["rule_id"], "education_terms_v1")
        self.assertEqual(json.loads(row["evidence_json"]), {
            "matched_terms": ["school", "summer camp"],
        })

    def test_42_unknown_classification_persists_without_fabricated_evidence(self) -> None:
        _result, row = self._persisted_classification(42, "Welcome to our page")
        self.assertEqual(row["classification_label"], "UNKNOWN")
        self.assertEqual(row["rule_id"], "no_supported_classification_v1")
        self.assertIsNone(row["evidence_json"])

    def test_43_classification_tracks_exact_observation_and_processing_runs(self) -> None:
        self.conn.execute(
            "INSERT INTO facebook_ads(id, platform, page_name) VALUES (43, 'facebook', 'Page Shop')"
        )
        for observation_id, text in ((430, "Compressor 9L"), (431, "Location de voiture")):
            self.conn.execute(
                """INSERT INTO facebook_ad_observations
                   (id, ad_id, observed_at, detected_metadata_json)
                   VALUES (?, 43, ?, ?)""",
                (observation_id, f"2026-10-07T02:00:{observation_id % 60:02d}Z",
                 json.dumps({"raw_source_payload": {"card_text": self.facebook_card(text)}})),
            )
        result = self.engine.process_observation(self.conn, "facebook_ad", 431)
        row = self.conn.execute(
            """SELECT c.source_ref_id, c.normalization_run_id, c.extraction_run_id,
                      r.source_observation_id, raw.id AS raw_observation_id,
                      x.normalization_run_id AS extraction_parent_id,
                      n.input_hash AS normalization_input_hash,
                      c.input_hash AS classification_input_hash,
                      c.classification_label, c.classified_at
               FROM classification_results c
               JOIN normalization_source_refs r ON r.id=c.source_ref_id
               JOIN facebook_ad_observations raw ON raw.id=r.facebook_observation_id
               JOIN normalization_runs n ON n.id=c.normalization_run_id
               JOIN normalization_extraction_runs x ON x.id=c.extraction_run_id
               WHERE c.id=?""",
            (result.classification_result_id,),
        ).fetchone()
        self.assertEqual(row["source_observation_id"], 431)
        self.assertEqual(row["raw_observation_id"], 431)
        self.assertEqual(row["normalization_run_id"], result.normalization_run_id)
        self.assertEqual(row["extraction_run_id"], result.extraction_run_id)
        self.assertEqual(row["extraction_parent_id"], row["normalization_run_id"])
        self.assertEqual(row["normalization_input_hash"], row["classification_input_hash"])
        self.assertTrue(row["classified_at"])
        self.assertEqual(row["classification_label"], "SERVICE")

    def test_44_reprocessing_classification_is_idempotent(self) -> None:
        self.add_facebook(44, 44, self.facebook_card("Compressor 9L"))
        first = self.engine.process_observation(self.conn, "facebook_ad", 44)
        second = self.engine.process_observation(self.conn, "facebook_ad", 44)
        self.assertEqual(first.classification_result_id, second.classification_result_id)
        self.assertEqual(self.table_count("classification_results"), 1)

    def test_45_new_classification_version_coexists_with_old_result(self) -> None:
        self.add_facebook(45, 45, self.facebook_card("Compressor 9L"))
        old_engine = NormalizationEngine(replace(
            DEFAULT_VERSIONS, classification_version="classification_v1",
        ))
        old = old_engine.process_observation(self.conn, "facebook_ad", 45)
        new_engine = NormalizationEngine(new_classification_version("classification_v2"))
        new = new_engine.process_observation(self.conn, "facebook_ad", 45)
        self.assertNotEqual(old.classification_result_id, new.classification_result_id)
        rows = self.conn.execute(
            """SELECT classification_version, classification_label
               FROM classification_results ORDER BY id"""
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("classification_v1", "PRODUCT"), ("classification_v2", "PRODUCT"),
        ])

    def test_35_process_all_uses_bounded_batches_and_respects_limit(self) -> None:
        for index in range(35, 40):
            self.add_facebook(index, index, self.facebook_card("Compressor 9L"))
        results = list(self.engine.process_all(
            self.conn, source_entity_types=("facebook_ad",), batch_size=2,
            max_observations=3,
        ))
        self.assertEqual(len(results), 3)
        self.assertEqual(self.table_count("normalization_runs"), 3)

    def test_36_source_input_path_uses_immutable_payload_fields(self) -> None:
        self.add_ouedkniss(40, 40, "entity title", "observation title")
        row = self.conn.execute(
            """SELECT l.raw_title, o.source_payload_json
               FROM marketplace_listings l JOIN marketplace_listing_observations o
                 ON o.listing_id=l.id WHERE o.id=40"""
        ).fetchone()
        selection = select_ouedkniss_text(row["source_payload_json"])
        self.assertEqual(selection.raw_text, "observation title")
        self.assertTrue(selection.field_path.startswith(
            "marketplace_listing_observations.source_payload_json"
        ))

    def test_37_matching_query_uses_alias_lookup_index(self) -> None:
        phrase = "Compressor 9L ABC-123"
        self.add_product(1, "Compressor")
        self.add_alias(1, phrase)
        self.add_facebook(41, 41, self.facebook_card(phrase))
        result = self.engine.process_observation(self.conn, "facebook_ad", 41)
        mention = self.conn.execute(
            "SELECT normalized_text FROM product_mentions WHERE id=?", (result.mention_ids[0],),
        ).fetchone()[0]
        plan = self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM product_aliases INDEXED BY ix_product_alias_lookup "
            "WHERE source_type=? AND normalized_alias=?", ("facebook", mention),
        ).fetchall()
        self.assertTrue(any("ix_product_alias_lookup" in str(tuple(row)) for row in plan))


if __name__ == "__main__":
    unittest.main()
