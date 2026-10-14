from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from normalization.worker_018_benchmark import run_benchmark


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DB = PROJECT_ROOT / "data" / "Database.sqlite"
MANIFEST = PROJECT_ROOT / "normalization" / "worker_018_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


class Worker018BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.production_hash_before = sha256(PRODUCTION_DB)
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="kratzwerk-worker018-")
        cls.benchmark_path = Path(cls.temp_dir.name) / "benchmark.sqlite"
        cls.results = run_benchmark(
            PRODUCTION_DB,
            cls.benchmark_path,
            manifest_path=MANIFEST,
        )
        cls.conn = sqlite3.connect(f"file:{cls.benchmark_path.as_posix()}?mode=ro", uri=True)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()
        cls.temp_dir.cleanup()

    def case(self, case_id: str) -> dict:
        return next(
            result for result in self.results["manifest_case_results"]
            if result["case_id"] == case_id
        )

    def test_unique_exact_alias_is_matched_high(self) -> None:
        case = self.case("unique_compressor_campaign")
        row = case["observations"][0]
        self.assertEqual(row["candidate_product_ids"], [18001])
        self.assertEqual((row["final_state"], row["confidence"]), ("MATCHED", "HIGH"))

    def test_deterministic_normalization_equivalents_match(self) -> None:
        cases = {
            "italian_soffitto": (18004, {32, 38}, {3}),
            "dash_normalized_fixa_mur": (18005, {33, 36}, {4}),
            "unicode_case_whitespace_satine": (18006, {31, 35}, {5}),
        }
        for case_id, (expected_product_id, retained, missing_history) in cases.items():
            with self.subTest(case_id=case_id):
                case = self.case(case_id)
                rows = {row["source_observation_id"]: row for row in case["observations"]}
                for observation_id in retained:
                    self.assertEqual(rows[observation_id]["candidate_product_ids"], [expected_product_id])
                    self.assertEqual(
                        (rows[observation_id]["final_state"], rows[observation_id]["confidence"]),
                        ("MATCHED", "HIGH"),
                    )
                for observation_id in missing_history:
                    self.assertEqual(rows[observation_id]["classification"], "UNKNOWN")
                    self.assertIsNone(rows[observation_id]["mention_index"])
                    self.assertEqual(rows[observation_id]["final_state"], "NO_MENTION")

    def test_ambiguous_alias_requires_review(self) -> None:
        case = self.case("ambiguous_fixateur_alias")
        self.assertEqual(len(case["observations"]), 3)
        rows = {row["source_observation_id"]: row for row in case["observations"]}
        self.assertEqual(rows[8]["final_state"], "NO_MENTION")
        for observation_id in (27, 37):
            row = rows[observation_id]
            self.assertEqual(row["candidate_product_ids"], [18005, 18009])
            self.assertEqual((row["final_state"], row["confidence"]), ("REVIEW", "UNCERTAIN"))

    def test_no_catalog_candidate_is_unmapped(self) -> None:
        case = self.case("no_catalog_lac_a_eau")
        rows = {row["source_observation_id"]: row for row in case["observations"]}
        self.assertEqual(rows[10]["final_state"], "NO_MENTION")
        for observation_id in (28, 39):
            row = rows[observation_id]
            self.assertEqual(row["candidate_product_ids"], [])
            self.assertEqual((row["final_state"], row["confidence"]), ("UNMAPPED", "NO_MATCH"))

    def test_explicit_category_conflict_uses_existing_review_rule(self) -> None:
        case = self.case("category_conflict_impronta_alias")
        rows = {row["source_observation_id"]: row for row in case["observations"]}
        self.assertEqual(rows[9]["final_state"], "NO_MENTION")
        for observation_id in (34, 40):
            row = rows[observation_id]
            self.assertEqual(row["candidate_product_ids"], [18001])
            self.assertEqual((row["final_state"], row["confidence"]), ("REVIEW", "UNCERTAIN"))
            self.assertEqual(row["decision_method"], "conflict_review_v1")
        conflict_rows = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_evidence WHERE evidence_type='deterministic_rule' "
            "AND evidence_value LIKE 'category_conflict:%'"
        ).fetchone()[0]
        self.assertEqual(conflict_rows, 2)

    def test_negative_and_non_product_cases_do_not_match(self) -> None:
        self.assertEqual(self.results["false_positive_benchmark_matches"], [])
        for row in self.case("semantic_paint_false_positive")["observations"]:
            self.assertEqual(row["classification"], "UNKNOWN")
            self.assertEqual(row["candidate_product_ids"], [])
            self.assertIsNone(row["mention_index"])
            self.assertEqual(row["final_state"], "NO_MENTION")
        for row in self.case("non_product_gate_probes")["observations"]:
            self.assertIn(row["classification"], {"UNKNOWN", "EDUCATION"})
            self.assertIsNone(row["mention_index"])
            self.assertEqual(row["final_state"], "NO_MENTION")
        self.assertEqual(len(self.results["manifest_expectation_deltas"]), 20)

    def test_identical_second_run_adds_no_derived_rows(self) -> None:
        result = self.results["idempotency"]
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["first_run_rows"]), 9)
        self.assertEqual(result["additional_rows"], {table: 0 for table in result["additional_rows"]})

    def test_candidate_provenance_reaches_source_alias_and_versions(self) -> None:
        provenance = self.results["provenance"]
        self.assertEqual(provenance["candidate_rows_traced"], provenance["candidate_rows"])
        self.assertEqual(provenance["candidate_evidence_rows_traced"], provenance["candidate_evidence_rows"])
        self.assertEqual(provenance["decision_events_traced"], provenance["decision_events"])
        row = self.conn.execute(
            """SELECT r.source_entity_type, r.source_observation_id,
                      fo.id AS facebook_observation_id, m.source_text_span,
                      p.canonical_name, e.evidence_type, e.product_alias_id,
                      a.raw_alias, g.matching_version, n.normalization_version,
                      x.extraction_version, d.decision_method
               FROM candidate_evidence e
               JOIN product_candidates c ON c.id=e.candidate_id
               JOIN candidate_generation_runs g ON g.id=c.candidate_run_id
               JOIN product_mentions m ON m.id=g.product_mention_id
               JOIN normalization_extraction_runs x ON x.id=m.extraction_run_id
               JOIN normalization_runs n ON n.id=x.normalization_run_id
               JOIN normalization_source_refs r ON r.id=n.source_ref_id
               JOIN facebook_ad_observations fo ON fo.id=r.facebook_observation_id
               JOIN products p ON p.id=c.product_id
               JOIN product_aliases a ON a.id=e.product_alias_id
               JOIN normalization_decision_events d ON d.product_mention_id=m.id
               WHERE r.source_entity_type='facebook_ad' AND r.source_observation_id=76
                 AND e.evidence_type='exact_alias' AND d.revision_no=1"""
        ).fetchone()
        self.assertEqual(row["facebook_observation_id"], 76)
        self.assertEqual(row["source_observation_id"], 76)
        self.assertEqual(row["source_text_span"], "🎉 LE COMPRESSEUR QUI CHANGE TOUT ! 🔧")
        self.assertEqual(row["canonical_name"], "Compressor campaign (unidentified model)")
        self.assertIsNotNone(row["product_alias_id"])
        self.assertEqual(row["evidence_type"], "exact_alias")
        self.assertEqual(row["normalization_version"], "deterministic_v1")
        self.assertEqual(row["matching_version"], "deterministic_match_v1")
        self.assertEqual(row["decision_method"], "unique_exact_alias_v1")

    def test_source_observations_remain_unchanged_in_benchmark_copy(self) -> None:
        self.assertTrue(self.results["source_observations_unchanged"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM facebook_ad_observations").fetchone()[0], 217,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM marketplace_listing_observations").fetchone()[0], 51,
        )

    def test_production_database_hash_and_empty_catalog_remain_unchanged(self) -> None:
        self.assertEqual(self.results["production_database_hash"]["before"], self.production_hash_before)
        self.assertEqual(self.results["production_database_hash"]["after"], self.production_hash_before)
        self.assertTrue(self.results["production_database_hash"]["unchanged"])
        self.assertEqual(
            self.results["production_catalog_counts"]["before"],
            {"products": 0, "product_aliases": 0, "product_mappings": 0},
        )
        self.assertEqual(sha256(PRODUCTION_DB), self.production_hash_before)

    def test_arabic_crown_model_is_extracted_and_matched(self) -> None:
        case = self.case("arabic_model_product_not_extracted")
        row = case["observations"][0]
        self.assertEqual(row["classification"], "PRODUCT")
        self.assertEqual(row["candidate_product_ids"], [18010])
        self.assertEqual((row["final_state"], row["confidence"]), ("MATCHED", "HIGH"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product_aliases WHERE id=18115 AND normalized_alias LIKE '%ct31012%'"
        ).fetchone()[0], 1)

    def test_similar_paint_titles_resolve_to_distinct_products(self) -> None:
        mat_acryl = self.case("similar_acrylique_mat_acryl")["observations"]
        black_mat = self.case("similar_acrylique_black_mat")["observations"]
        for rows, product_id, missing_observation in (
            (mat_acryl, 18007, 6), (black_mat, 18008, 7),
        ):
            by_observation = {row["source_observation_id"]: row for row in rows}
            self.assertEqual(by_observation[missing_observation]["final_state"], "NO_MENTION")
            retained = [row for key, row in by_observation.items() if key != missing_observation]
            self.assertTrue(all(row["candidate_product_ids"] == [product_id] for row in retained))
        mat_products = {
            row["candidate_product_ids"][0] for row in mat_acryl
            if row["candidate_product_ids"]
        }
        black_products = {
            row["candidate_product_ids"][0] for row in black_mat
            if row["candidate_product_ids"]
        }
        self.assertTrue(mat_products.isdisjoint(black_products))

    def test_measurements_retain_declared_corpus_baseline(self) -> None:
        self.assertEqual(self.results["input"]["observations_by_source"], {
            "facebook_ad": 217,
            "marketplace_listing": 51,
        })
        self.assertEqual(self.results["input"]["facebook_usable_text_observations"], 129)
        self.assertEqual(
            self.results["input"]["source_input_provenance"]["marketplace_entity_title_fallback_observations"],
            0,
        )
        self.assertEqual(
            self.results["input"]["source_input_provenance"]["marketplace_product_mentions_from_entity_title_fallback"],
            0,
        )
        self.assertEqual(
            self.results["input"]["source_input_provenance"]["marketplace_observation_local_title_coverage"],
            34,
        )
        self.assertEqual(
            self.results["input"]["source_input_provenance"]["facebook_non_observation_input_rows"],
            0,
        )
        self.assertEqual(self.results["candidate_generation"]["product_mentions"], 23)
        self.assertEqual(self.results["candidate_generation"]["outcomes"], {
            "MATCHED": 17,
            "REVIEW": 4,
            "UNMAPPED": 2,
            "REJECTED": 0,
            "CANDIDATE": 0,
        })


if __name__ == "__main__":
    unittest.main()
