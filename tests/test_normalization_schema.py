from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_SCHEMA = ROOT / "tests" / "fixtures" / "normalization_base.sql"
NORMALIZATION_SCHEMA = ROOT / "normalization" / "schema_v1.sql"


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalization_input_hash(field_path: str, raw_text: str) -> str:
    return sha256_text(field_path + "\0" + raw_text)


class NormalizationSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kratzwerk-normalization-")
        self.db_path = Path(self.temp.name) / "normalization-test.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(BASE_SCHEMA.read_text(encoding="utf-8"))
        self.conn.executescript(NORMALIZATION_SCHEMA.read_text(encoding="utf-8"))
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def add_product(self, product_id: int, name: str, category: str = "equipment") -> int:
        self.conn.execute(
            "INSERT INTO products(id, canonical_name, category) VALUES (?, ?, ?)",
            (product_id, name, category),
        )
        return product_id

    def add_marketplace_source(
        self, listing_id: int, observation_id: int, text: str,
        platform: str = "Ouedkniss", observed_at: str = "2026-10-01T00:00:00Z",
    ) -> int:
        self.conn.execute(
            "INSERT INTO marketplace_listings(id, platform, raw_title) VALUES (?, ?, ?)",
            (listing_id, platform, text),
        )
        self.conn.execute(
            """INSERT INTO marketplace_listing_observations
               (id, listing_id, observed_at, status) VALUES (?, ?, ?, 'active')""",
            (observation_id, listing_id, observed_at),
        )
        cur = self.conn.execute(
            """INSERT INTO normalization_source_refs
               (source_platform, source_entity_type, source_record_id,
                source_observation_id, marketplace_observation_id)
               VALUES (?, 'marketplace_listing', ?, ?, ?)""",
            (platform, listing_id, observation_id, observation_id),
        )
        return int(cur.lastrowid)

    def add_facebook_source(
        self, ad_id: int, observation_id: int, text: str,
        platform: str = "facebook", observed_at: str = "2026-10-01T00:00:00Z",
        detected_metadata_json: str = "{}",
    ) -> int:
        self.conn.execute(
            "INSERT INTO facebook_ads(id, platform, creative_text) VALUES (?, ?, ?)",
            (ad_id, platform, text),
        )
        self.conn.execute(
            """INSERT INTO facebook_ad_observations
               (id, ad_id, observed_at, detected_metadata_json) VALUES (?, ?, ?, ?)""",
            (observation_id, ad_id, observed_at, detected_metadata_json),
        )
        cur = self.conn.execute(
            """INSERT INTO normalization_source_refs
               (source_platform, source_entity_type, source_record_id,
                source_observation_id, facebook_observation_id)
               VALUES (?, 'facebook_ad', ?, ?, ?)""",
            (platform, ad_id, observation_id, observation_id),
        )
        return int(cur.lastrowid)

    def add_normalization(
        self, source_ref_id: int, source_text: str, normalized_text: str | None = None,
        version: str = "text-v1", field_path: str = "creative_text",
        selector_version: str = "source_text_selector_v1",
        input_span: tuple[int, int] | None = None,
    ) -> int:
        if normalized_text is None:
            normalized_text = source_text.casefold()
        self.conn.execute(
            """INSERT OR IGNORE INTO normalization_runs
               (source_ref_id, normalization_version, selector_version, input_field_path,
                input_span_start, input_span_end, selected_input_text, input_hash,
                normalized_text, language_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (source_ref_id, version, selector_version, field_path,
             input_span[0] if input_span else None,
             input_span[1] if input_span else None,
             source_text, normalization_input_hash(field_path, source_text), normalized_text),
        )
        row = self.conn.execute(
            """SELECT id, normalized_text FROM normalization_runs
               WHERE source_ref_id=? AND normalization_version=? AND selector_version=?
                 AND input_hash=?""",
            (source_ref_id, version, selector_version,
             normalization_input_hash(field_path, source_text)),
        ).fetchone()
        if row["normalized_text"] != normalized_text:
            raise AssertionError("same version/input hash produced a different normalized result")
        return int(row["id"])

    def add_extraction(self, normalization_run_id: int, normalized_text: str,
                       version: str = "extract-v1", status: str = "complete") -> int:
        cur = self.conn.execute(
            """INSERT INTO normalization_extraction_runs
               (normalization_run_id, extraction_version, input_hash, status)
               VALUES (?, ?, ?, ?)""",
            (normalization_run_id, version, sha256_text(normalized_text), status),
        )
        return int(cur.lastrowid)

    def add_mention(
        self, extraction_run_id: int, index: int, source_text: str,
        mention_text: str, start: int, *, kind: str = "product",
        normalized_text: str | None = None, **attributes: object,
    ) -> int:
        if normalized_text is None:
            normalized_text = mention_text.casefold()
        allowed = {
            "language_code", "brand", "model", "category", "variant",
            "quantity_value", "quantity_unit", "capacity_value", "capacity_unit",
            "specifications_json", "extraction_method", "extraction_confidence",
            "extraction_score", "extraction_score_type", "ai_provider_run_id",
        }
        unexpected = set(attributes) - allowed
        if unexpected:
            raise ValueError(f"Unexpected mention attributes: {sorted(unexpected)}")
        values = {
            "language_code": None, "brand": None, "model": None, "category": None,
            "variant": None, "quantity_value": None, "quantity_unit": None,
            "capacity_value": None, "capacity_unit": None,
            "specifications_json": None, "extraction_method": "deterministic_rule",
            "extraction_confidence": None, "extraction_score": None,
            "extraction_score_type": None, "ai_provider_run_id": None,
        }
        values.update(attributes)
        cursor = self.conn.execute(
            """INSERT INTO product_mentions (
                   extraction_run_id, mention_index, source_span_start, source_span_end,
                   source_text_span, normalized_text, language_code, mention_kind,
                   brand, model, category, variant, quantity_value, quantity_unit,
                   capacity_value, capacity_unit, specifications_json, extraction_method,
                   extraction_confidence, extraction_score, extraction_score_type,
                   ai_provider_run_id
               ) VALUES (
                   :extraction_run_id, :mention_index, :source_span_start, :source_span_end,
                   :source_text_span, :normalized_text, :language_code, :mention_kind,
                   :brand, :model, :category, :variant, :quantity_value, :quantity_unit,
                   :capacity_value, :capacity_unit, :specifications_json, :extraction_method,
                   :extraction_confidence, :extraction_score, :extraction_score_type,
                   :ai_provider_run_id
               )""",
            {
                "extraction_run_id": extraction_run_id,
                "mention_index": index,
                "source_span_start": start,
                "source_span_end": start + len(mention_text),
                "source_text_span": mention_text,
                "normalized_text": normalized_text,
                "mention_kind": kind,
                **values,
            },
        )
        return int(cursor.lastrowid)

    def add_candidate_run(self, mention_id: int, suffix: str = "catalog-v1",
                          method: str = "deterministic") -> int:
        value = f"mention={mention_id};catalog={suffix}"
        cur = self.conn.execute(
            """INSERT INTO candidate_generation_runs
               (product_mention_id, matching_version, catalog_snapshot_hash, input_hash,
                generation_method, status)
               VALUES (?, 'matcher-v1', ?, ?, ?, 'complete')""",
            (mention_id, sha256_text(suffix), sha256_text(value), method),
        )
        return int(cur.lastrowid)

    def add_candidate(self, candidate_run_id: int, product_id: int,
                      confidence: str = "UNCERTAIN", score: float | None = None,
                      score_type: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO product_candidates
               (candidate_run_id, product_id, confidence_label, score, score_type,
                scoring_method_version)
               VALUES (?, ?, ?, ?, ?, 'matcher-v1')""",
            (candidate_run_id, product_id, confidence, score, score_type),
        )
        return int(cur.lastrowid)

    def add_evidence(self, candidate_id: int, evidence_type: str,
                     value: str, *, source: str = "mention_text",
                     ai_run_id: int | None = None, alias_id: int | None = None,
                     method_version: str | None = "rule-v1") -> int:
        cur = self.conn.execute(
            """INSERT INTO candidate_evidence
               (candidate_id, evidence_type, evidence_value, evidence_source,
                method_version, product_alias_id, ai_provider_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, evidence_type, value, source, method_version, alias_id, ai_run_id),
        )
        return int(cur.lastrowid)

    def add_decision(self, mention_id: int, revision: int, state: str,
                     candidate_id: int | None = None, product_id: int | None = None,
                     actor_type: str = "human", actor_id: str | None = "reviewer-1",
                     method: str = "review", confidence: str | None = None,
                     reason: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO normalization_decision_events
               (product_mention_id, revision_no, decision_state, candidate_id, product_id,
                actor_type, actor_id, decision_method, decision_method_version,
                confidence_label, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'decision-v1', ?, ?)""",
            (mention_id, revision, state, candidate_id, product_id, actor_type,
             actor_id, method, confidence, reason),
        )
        return int(cur.lastrowid)

    def add_ai_run(self, *, status: str = "succeeded", request_id: str | None = None,
                   error_code: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO ai_provider_runs
               (provider, model, provider_request_id, prompt_version, schema_version,
                input_hash, output_version, status, started_at, completed_at, error_code)
               VALUES ('provider-x', 'model-y', ?, 'prompt-v1', 'schema-v1', ?,
                       'result-v1', ?, '2026-10-01T00:00:00Z',
                       '2026-10-01T00:00:01Z', ?)""",
            (request_id, sha256_text("ai input"), status, error_code),
        )
        return int(cur.lastrowid)

    def make_mention(self, text: str, source: str = "facebook") -> tuple[int, int, int]:
        if source == "marketplace":
            source_ref_id = self.add_marketplace_source(100, 200, text)
        else:
            source_ref_id = self.add_facebook_source(100, 200, text)
        norm_id = self.add_normalization(source_ref_id, text, text.casefold())
        extraction_id = self.add_extraction(norm_id, text.casefold())
        mention_id = self.add_mention(extraction_id, 0, text, text, 0)
        return source_ref_id, norm_id, mention_id

    def test_01_marketplace_observation_to_one_mention(self) -> None:
        source_ref_id = self.add_marketplace_source(1, 1, "SOFFITTO paint")
        norm_id = self.add_normalization(source_ref_id, "SOFFITTO paint", "soffitto paint",
                                         field_path="marketplace_listings.raw_title")
        extraction_id = self.add_extraction(norm_id, "soffitto paint")
        mention_id = self.add_mention(
            extraction_id, 0, "SOFFITTO paint", "SOFFITTO", 0, category="paint",
        )
        row = self.conn.execute(
            """SELECT r.source_platform, r.source_entity_type, o.id, m.source_text_span
               FROM product_mentions m
               JOIN normalization_extraction_runs e ON e.id=m.extraction_run_id
               JOIN normalization_runs n ON n.id=e.normalization_run_id
               JOIN normalization_source_refs r ON r.id=n.source_ref_id
               JOIN marketplace_listing_observations o ON o.id=r.marketplace_observation_id
               WHERE m.id=?""", (mention_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("Ouedkniss", "marketplace_listing", 1, "SOFFITTO"))

    def test_02_facebook_observation_can_have_multiple_mentions(self) -> None:
        text = "Compressor 9L + accessory kit"
        source_ref_id = self.add_facebook_source(2, 2, text)
        norm_id = self.add_normalization(source_ref_id, text, text.casefold())
        extraction_id = self.add_extraction(norm_id, text.casefold())
        first = self.add_mention(extraction_id, 0, text, "Compressor 9L", 0,
                                 capacity_value=9, capacity_unit="L")
        second_start = text.index("accessory kit")
        second = self.add_mention(extraction_id, 1, text, "accessory kit", second_start,
                                  kind="accessory")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_mentions WHERE extraction_run_id=?", (extraction_id,),
        ).fetchone()[0], 2)
        self.assertNotEqual(first, second)

    def test_03_many_observations_can_resolve_to_same_product(self) -> None:
        self.add_product(1, "Compressor")
        candidate_ids = []
        for i in (1, 2):
            source_ref_id = self.add_facebook_source(i, i, f"Compressor listing {i}")
            text = f"Compressor listing {i}"
            norm_id = self.add_normalization(source_ref_id, text, text.casefold())
            extraction_id = self.add_extraction(norm_id, text.casefold())
            mention_id = self.add_mention(extraction_id, 0, text, "Compressor", 0)
            run_id = self.add_candidate_run(mention_id)
            candidate_id = self.add_candidate(run_id, 1, "HIGH")
            self.add_decision(mention_id, 1, "MATCHED", candidate_id, 1, actor_type="rule",
                              actor_id="exact-model-rule", method="deterministic_match")
            candidate_ids.append(candidate_id)
        self.assertEqual(len(set(candidate_ids)), 2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM confirmed_product_mentions WHERE product_id=1"
        ).fetchone()[0], 2)

    def test_04_mention_can_have_zero_candidates_and_be_unmapped(self) -> None:
        _, _, mention_id = self.make_mention("Ambiguous or absent product")
        run_id = self.add_candidate_run(mention_id)
        self.add_decision(mention_id, 1, "UNMAPPED", actor_type="system",
                          actor_id="matcher", method="candidate_search",
                          confidence="NO_MATCH")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_candidates WHERE candidate_run_id=?", (run_id,),
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT decision_state FROM normalization_decision_events WHERE product_mention_id=?",
            (mention_id,),
        ).fetchone()[0], "UNMAPPED")

    def test_05_mention_can_have_one_candidate(self) -> None:
        self.add_product(1, "Paint")
        _, _, mention_id = self.make_mention("SOFFITTO")
        run_id = self.add_candidate_run(mention_id)
        candidate_id = self.add_candidate(run_id, 1, "PROBABLE", 0.71,
                                          "uncalibrated_similarity")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_candidates WHERE candidate_run_id=?", (run_id,),
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT score_type FROM product_candidates WHERE id=?", (candidate_id,),
        ).fetchone()[0], "uncalibrated_similarity")

    def test_06_mention_can_have_multiple_candidates(self) -> None:
        self.add_product(1, "Compressor A")
        self.add_product(2, "Compressor B")
        _, _, mention_id = self.make_mention("Compressor")
        run_id = self.add_candidate_run(mention_id)
        self.add_candidate(run_id, 1)
        self.add_candidate(run_id, 2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_candidates WHERE candidate_run_id=?", (run_id,),
        ).fetchone()[0], 2)

    def test_07_rejected_candidate_does_not_block_later_match(self) -> None:
        self.add_product(1, "Wrong compressor")
        self.add_product(2, "Correct compressor")
        _, _, mention_id = self.make_mention("Compressor Model Z")
        run_id = self.add_candidate_run(mention_id)
        rejected_id = self.add_candidate(run_id, 1)
        accepted_id = self.add_candidate(run_id, 2, "HIGH")
        self.add_decision(mention_id, 1, "REJECTED", rejected_id, 1,
                          actor_type="human", reason="model does not agree")
        self.add_decision(mention_id, 2, "CANDIDATE", accepted_id, 2,
                          actor_type="rule", method="alternative_candidate")
        self.add_decision(mention_id, 3, "MATCHED", accepted_id, 2,
                          actor_type="human", method="review_approval")
        row = self.conn.execute(
            "SELECT candidate_id, product_id FROM confirmed_product_mentions WHERE product_mention_id=?",
            (mention_id,),
        ).fetchone()
        self.assertEqual(tuple(row), (accepted_id, 2))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM normalization_decision_events WHERE product_mention_id=?",
            (mention_id,),
        ).fetchone()[0], 3)

    def test_08_ai_suggestion_is_evidence_not_a_confirmed_mapping(self) -> None:
        self.add_product(1, "Candidate product")
        _, _, mention_id = self.make_mention("Possible product")
        run_id = self.add_candidate_run(mention_id, method="ai_suggestion")
        candidate_id = self.add_candidate(run_id, 1)
        ai_run_id = self.add_ai_run(request_id="request-1")
        self.add_evidence(candidate_id, "ai_suggestion", "Possible match from model",
                          ai_run_id=ai_run_id, source="provider_output", method_version=None)
        self.add_decision(mention_id, 1, "CANDIDATE", candidate_id, 1,
                          actor_type="ai", actor_id="provider-x/model-y",
                          method="ai_suggestion")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM candidate_evidence WHERE ai_provider_run_id=?", (ai_run_id,),
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM confirmed_product_mentions WHERE product_mention_id=?",
            (mention_id,),
        ).fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_decision(mention_id, 2, "MATCHED", candidate_id, 1,
                              actor_type="ai", actor_id="provider-x", method="ai_match")

    def test_09_human_review_is_recorded_in_append_only_history(self) -> None:
        self.add_product(1, "Reviewed product")
        _, _, mention_id = self.make_mention("Reviewed title")
        candidate_id = self.add_candidate(self.add_candidate_run(mention_id), 1)
        event_id = self.add_decision(mention_id, 1, "REVIEW", candidate_id, 1,
                                     actor_type="human", actor_id="reviewer-42",
                                     method="human_review", reason="verify variant")
        row = self.conn.execute(
            "SELECT actor_type, actor_id, decision_state, reason FROM normalization_decision_events WHERE id=?",
            (event_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("human", "reviewer-42", "REVIEW", "verify variant"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE normalization_decision_events SET reason='changed' WHERE id=?", (event_id,),
            )

    def test_10_same_observation_version_and_hash_is_idempotent(self) -> None:
        source_ref_id = self.add_facebook_source(10, 10, "same input")
        first = self.add_normalization(source_ref_id, "same input", "same input")
        second = self.add_normalization(source_ref_id, "same input", "same input")
        self.assertEqual(first, second)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM normalization_runs WHERE source_ref_id=?", (source_ref_id,),
        ).fetchone()[0], 1)

    def test_normalization_field_path_is_part_of_the_input_hash(self) -> None:
        source_ref_id = self.add_facebook_source(12, 12, "same text")
        creative = self.add_normalization(source_ref_id, "same text", "same text",
                                          field_path="creative_text")
        card = self.add_normalization(source_ref_id, "same text", "same text",
                                      field_path="detected_metadata_json.card_text")
        self.assertNotEqual(creative, card)

    def test_selector_version_is_part_of_normalization_run_identity(self) -> None:
        source_ref_id = self.add_facebook_source(13, 13, "same selected text")
        first = self.add_normalization(
            source_ref_id, "same selected text", selector_version="selector-v1",
        )
        second = self.add_normalization(
            source_ref_id, "same selected text", selector_version="selector-v2",
        )
        self.assertNotEqual(first, second)

    def test_source_span_offsets_must_be_present_as_a_valid_pair(self) -> None:
        source_ref_id = self.add_facebook_source(15, 15, "selected text")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO normalization_runs
                   (source_ref_id, normalization_version, selector_version,
                    input_field_path, input_span_start, input_span_end,
                    selected_input_text, input_hash, normalized_text)
                   VALUES (?, 'text-v1', 'selector-v1', 'payload.card_text',
                           0, NULL, 'selected text', 'hash', 'selected text')""",
                (source_ref_id,),
            )

    def test_normalization_run_preserves_source_and_selected_input_provenance_separately(self) -> None:
        card = "Sponsored\nObserved Page\nCompressor 9L"
        observed_at = "2026-10-03T12:34:56Z"
        metadata = json.dumps({
            "page_name": "Observed Page",
            "raw_source_payload": {"card_text": card},
        })
        source_ref_id = self.add_facebook_source(
            14, 14, "entity value is not selected", observed_at=observed_at,
            detected_metadata_json=metadata,
        )
        field_path = "facebook_ad_observations.detected_metadata_json.raw_source_payload.card_text"
        start = card.index("Compressor 9L")
        end = start + len("Compressor 9L")
        run_id = self.add_normalization(
            source_ref_id, "Compressor 9L", field_path=field_path, input_span=(start, end),
        )
        row = self.conn.execute(
            """SELECT r.observed_at, r.detected_metadata_json,
                      n.selected_input_text, n.input_field_path, n.input_span_start,
                      n.input_span_end, n.input_hash, n.selector_version,
                      n.normalization_version
               FROM normalization_runs n
               JOIN normalization_source_refs s ON s.id=n.source_ref_id
               JOIN facebook_ad_observations r ON r.id=s.facebook_observation_id
               WHERE n.id=?""", (run_id,),
        ).fetchone()
        self.assertEqual(row["observed_at"], observed_at)
        self.assertEqual(row["selected_input_text"], "Compressor 9L")
        self.assertEqual(row["input_field_path"], field_path)
        self.assertEqual((row["input_span_start"], row["input_span_end"]), (start, end))
        self.assertEqual(row["input_hash"], normalization_input_hash(field_path, "Compressor 9L"))
        self.assertEqual(row["selector_version"], "source_text_selector_v1")
        self.assertEqual(row["normalization_version"], "text-v1")
        self.assertEqual(json.loads(row["detected_metadata_json"])["page_name"], "Observed Page")

    def test_11_new_normalization_version_coexists_with_old_result(self) -> None:
        source_ref_id = self.add_facebook_source(11, 11, "Input with accents")
        old_id = self.add_normalization(source_ref_id, "Input with accents", "old", "text-v1")
        new_id = self.add_normalization(source_ref_id, "Input with accents", "new", "text-v2")
        self.assertNotEqual(old_id, new_id)
        rows = [tuple(row) for row in self.conn.execute(
            "SELECT normalization_version, normalized_text FROM normalization_runs ORDER BY id"
        )]
        self.assertEqual(rows, [("text-v1", "old"), ("text-v2", "new")])

    def test_12_same_numeric_ids_on_different_platforms_do_not_collide(self) -> None:
        marketplace_ref = self.add_marketplace_source(1, 1, "market item", "Ouedkniss")
        facebook_ref = self.add_facebook_source(1, 1, "facebook ad", "facebook")
        refs = self.conn.execute(
            "SELECT id, source_platform, source_entity_type, source_record_id "
            "FROM normalization_source_refs ORDER BY id"
        ).fetchall()
        self.assertEqual(len(refs), 2)
        self.assertNotEqual(marketplace_ref, facebook_ref)
        self.assertEqual({(r["source_platform"], r["source_entity_type"], r["source_record_id"])
                          for r in refs}, {
            ("Ouedkniss", "marketplace_listing", 1), ("facebook", "facebook_ad", 1),
        })

    def test_13_raw_source_observation_is_unchanged_and_immutable(self) -> None:
        source_ref_id = self.add_facebook_source(20, 20, "raw copy")
        before = tuple(self.conn.execute(
            "SELECT id, ad_id, observed_at, detected_metadata_json "
            "FROM facebook_ad_observations WHERE id=20"
        ).fetchone())
        norm_id = self.add_normalization(source_ref_id, "raw copy", "normalized copy")
        extraction_id = self.add_extraction(norm_id, "normalized copy")
        self.add_mention(extraction_id, 0, "raw copy", "raw copy", 0)
        after = tuple(self.conn.execute(
            "SELECT id, ad_id, observed_at, detected_metadata_json "
            "FROM facebook_ad_observations WHERE id=20"
        ).fetchone())
        self.assertEqual(before, after)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE facebook_ad_observations SET detected_metadata_json='{}' WHERE id=20"
            )

    def test_14_evidence_traces_through_mention_to_exact_observation(self) -> None:
        self.add_product(1, "Tracked product")
        source_ref_id = self.add_marketplace_source(30, 31, "Tracked product title")
        norm_id = self.add_normalization(source_ref_id, "Tracked product title", "tracked product title")
        extraction_id = self.add_extraction(norm_id, "tracked product title")
        mention_id = self.add_mention(extraction_id, 0, "Tracked product title", "Tracked product", 0)
        run_id = self.add_candidate_run(mention_id)
        candidate_id = self.add_candidate(run_id, 1)
        evidence_id = self.add_evidence(candidate_id, "normalized_title", "tracked product")
        self.add_decision(mention_id, 1, "CANDIDATE", candidate_id, 1,
                          actor_type="rule", method="title_match")
        row = self.conn.execute(
            """SELECT r.source_platform, r.source_observation_id, o.listing_id,
                      n.normalization_version, n.input_hash, m.source_text_span,
                      e.evidence_type, e.evidence_value, d.decision_state
               FROM candidate_evidence e
               JOIN product_candidates c ON c.id=e.candidate_id
               JOIN candidate_generation_runs g ON g.id=c.candidate_run_id
               JOIN product_mentions m ON m.id=g.product_mention_id
               JOIN normalization_extraction_runs x ON x.id=m.extraction_run_id
               JOIN normalization_runs n ON n.id=x.normalization_run_id
               JOIN normalization_source_refs r ON r.id=n.source_ref_id
               JOIN marketplace_listing_observations o ON o.id=r.marketplace_observation_id
               JOIN normalization_decision_events d ON d.product_mention_id=m.id
               WHERE e.id=?""", (evidence_id,),
        ).fetchone()
        self.assertEqual(row["source_platform"], "Ouedkniss")
        self.assertEqual(row["source_observation_id"], 31)
        self.assertEqual(row["listing_id"], 30)
        self.assertEqual(row["normalization_version"], "text-v1")
        self.assertEqual(row["source_text_span"], "Tracked product")
        self.assertEqual(row["evidence_type"], "normalized_title")
        self.assertEqual(row["decision_state"], "CANDIDATE")

    def test_15_unsupported_attributes_remain_null(self) -> None:
        _, _, mention_id = self.make_mention("Generic product title")
        row = self.conn.execute(
            """SELECT brand, model, category, variant, quantity_value, quantity_unit,
                      capacity_value, capacity_unit, specifications_json
               FROM product_mentions WHERE id=?""", (mention_id,),
        ).fetchone()
        self.assertEqual(tuple(row), (None,) * 9)

    def test_16_bundle_and_accessory_mentions_are_independent(self) -> None:
        self.add_product(1, "Compressor")
        self.add_product(2, "Accessory kit")
        text = "Compressor 9L plus accessory kit"
        source_ref_id = self.add_facebook_source(40, 40, text)
        norm_id = self.add_normalization(source_ref_id, text, text.casefold())
        extraction_id = self.add_extraction(norm_id, text.casefold())
        compressor_id = self.add_mention(extraction_id, 0, text, "Compressor 9L", 0,
                                         capacity_value=9, capacity_unit="L")
        accessory_start = text.index("accessory kit")
        accessory_id = self.add_mention(extraction_id, 1, text, "accessory kit", accessory_start,
                                        kind="accessory")
        for mention_id, product_id in ((compressor_id, 1), (accessory_id, 2)):
            candidate_id = self.add_candidate(self.add_candidate_run(mention_id), product_id)
            self.add_decision(mention_id, 1, "MATCHED", candidate_id, product_id,
                              actor_type="human", method="review_approval")
        rows = self.conn.execute(
            "SELECT product_mention_id, product_id FROM confirmed_product_mentions ORDER BY product_id"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(compressor_id, 1), (accessory_id, 2)])

    def test_17_failed_and_partial_ai_runs_are_recordable(self) -> None:
        failed = self.add_ai_run(status="failed", request_id="request-fail", error_code="timeout")
        partial = self.add_ai_run(status="partial", request_id="request-partial")
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status, error_code FROM ai_provider_runs WHERE id=?", (failed,),
        ).fetchone()), ("failed", "timeout"))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM ai_provider_runs WHERE id=?", (partial,),
        ).fetchone()[0], "partial")

    def test_18_transaction_rollback_leaves_no_partial_derived_state(self) -> None:
        source_ref_id = self.add_facebook_source(50, 50, "rollback test")
        self.conn.commit()
        self.conn.execute("BEGIN")
        norm_id = self.add_normalization(source_ref_id, "rollback test", "rollback test")
        extraction_id = self.add_extraction(norm_id, "rollback test")
        mention_id = self.add_mention(extraction_id, 0, "rollback test", "rollback test", 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO candidate_generation_runs
                   (product_mention_id, matching_version, catalog_snapshot_hash, input_hash,
                    generation_method, status)
                   VALUES (999999, 'matcher-v1', 'catalog', 'input', 'deterministic', 'complete')"""
            )
        self.conn.rollback()
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM normalization_runs WHERE source_ref_id=?", (source_ref_id,),
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM normalization_extraction_runs WHERE id=?", (extraction_id,),
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_mentions WHERE id=?", (mention_id,),
        ).fetchone()[0], 0)

    def test_source_reference_platform_must_match_raw_observation(self) -> None:
        self.add_marketplace_source(60, 60, "title")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO normalization_source_refs
                   (source_platform, source_entity_type, source_record_id,
                    source_observation_id, marketplace_observation_id)
                   VALUES ('facebook', 'marketplace_listing', 60, 60, 60)"""
            )

    def test_decision_cannot_reference_candidate_from_another_mention(self) -> None:
        self.add_product(1, "Candidate product")
        ref_a = self.add_facebook_source(101, 201, "First mention")
        ref_b = self.add_facebook_source(102, 202, "Second mention")
        norm_a = self.add_normalization(ref_a, "First mention", "first mention")
        norm_b = self.add_normalization(ref_b, "Second mention", "second mention")
        mention_a = self.add_mention(
            self.add_extraction(norm_a, "first mention"), 0, "First mention", "First mention", 0,
        )
        mention_b = self.add_mention(
            self.add_extraction(norm_b, "second mention"), 0, "Second mention", "Second mention", 0,
        )
        candidate_a = self.add_candidate(self.add_candidate_run(mention_a), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_decision(mention_b, 1, "MATCHED", candidate_a, 1)

    def test_alias_product_mismatch_is_rejected_for_candidates_and_legacy_mappings(self) -> None:
        self.add_product(1, "Product 1")
        self.add_product(2, "Product 2")
        self.conn.execute(
            """INSERT INTO product_aliases(id, product_id, source_type, raw_alias, normalized_alias)
               VALUES (1, 1, 'manual', 'alias one', 'alias one')"""
        )
        _, _, mention_id = self.make_mention("alias one")
        candidate_id = self.add_candidate(self.add_candidate_run(mention_id), 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_evidence(candidate_id, "exact_alias", "alias one", alias_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO product_mappings
                   (source_type, source_id, product_id, alias_id, mapping_source)
                   VALUES ('facebook_ad', 100, 2, 1, 'test')"""
            )

    def test_schema_has_no_foreign_key_violations(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_classification_label_registry_supports_controlled_extension(self) -> None:
        labels = {row[0] for row in self.conn.execute(
            "SELECT label FROM classification_labels"
        )}
        self.assertEqual(labels, {"PRODUCT", "SERVICE", "REAL_ESTATE", "EDUCATION", "UNKNOWN"})
        self.conn.execute(
            "INSERT INTO classification_labels(label) VALUES ('VEHICLE_RENTAL')"
        )
        self.assertIn("VEHICLE_RENTAL", {
            row[0] for row in self.conn.execute("SELECT label FROM classification_labels")
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO classification_labels(label) VALUES ('vehicle rental')"
            )


if __name__ == "__main__":
    unittest.main()
