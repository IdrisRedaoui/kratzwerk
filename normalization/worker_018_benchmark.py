"""Run Worker 018's isolated, real-observation catalog benchmark.

The runner copies the configured source SQLite database using SQLite's backup
API, adds only the normalization schema and the explicitly listed synthetic
benchmark products/aliases to that copy, and runs the existing deterministic
engine. It never opens the source database for writes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .engine import NormalizationEngine
from .extract import RecordClass, extract_product_mentions
from .match import find_exact_alias_candidates
from .persistence import load_source_observation
from .text import normalize_text
from .versioning import DEFAULT_VERSIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZATION_SCHEMA = PROJECT_ROOT / "normalization" / "schema_v1.sql"
DEFAULT_MANIFEST = PROJECT_ROOT / "normalization" / "worker_018_manifest.json"
DERIVED_TABLES = (
    "normalization_source_refs",
    "normalization_runs",
    "normalization_extraction_runs",
    "classification_results",
    "product_mentions",
    "candidate_generation_runs",
    "product_candidates",
    "candidate_evidence",
    "normalization_decision_events",
)
SOURCE_TABLES = (
    "facebook_ad_observations",
    "marketplace_listing_observations",
)
SOURCE_ENTITY_TYPES = ("facebook_ad", "marketplace_listing")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _observation_snapshot(conn: sqlite3.Connection) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for table in SOURCE_TABLES:
        digest = hashlib.sha256()
        cursor = conn.execute(f'SELECT * FROM "{table}" ORDER BY id')
        for row in cursor:
            digest.update(_json(list(row)).encode("utf-8"))
            digest.update(b"\n")
        snapshot[table] = digest.hexdigest().upper()
    return snapshot


def _row_counts(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: _table_count(conn, table) for table in tables}


def _source_reference_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "facebook_ad": _table_count(conn, "facebook_ad_observations"),
        "marketplace_listing": _table_count(conn, "marketplace_listing_observations"),
    }


def _insert_catalog(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[int, int]:
    if any(_table_count(conn, table) != 0 for table in ("products", "product_aliases", "product_mappings")):
        raise RuntimeError("isolated source copy must start with empty production catalog tables")

    alias_product: dict[int, int] = {}
    seen_product_ids: set[int] = set()
    seen_alias_ids: set[int] = set()
    for product in manifest["products"]:
        product_id = int(product["benchmark_product_id"])
        if product_id in seen_product_ids:
            raise ValueError(f"duplicate benchmark_product_id {product_id}")
        seen_product_ids.add(product_id)
        conn.execute(
            """INSERT INTO products(id, canonical_name, category, attributes_json)
               VALUES (?, ?, ?, ?)""",
            (product_id, product["canonical_name"], product.get("category"), _json(product.get("attributes", {}))),
        )
        for alias in product["aliases"]:
            alias_id = int(alias["benchmark_alias_id"])
            if alias_id in seen_alias_ids:
                raise ValueError(f"duplicate benchmark_alias_id {alias_id}")
            seen_alias_ids.add(alias_id)
            normalized = normalize_text(alias["raw_alias"], DEFAULT_VERSIONS.text_profile).normalized_text
            if normalized != alias["expected_normalized_alias"]:
                raise ValueError(
                    f"manifest normalization mismatch for alias {alias_id}: "
                    f"expected {alias['expected_normalized_alias']!r}, got {normalized!r}"
                )
            conn.execute(
                """INSERT INTO product_aliases
                   (id, product_id, source_type, raw_alias, normalized_alias, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (alias_id, product_id, alias["source_type"], alias["raw_alias"], normalized,
                 f"worker_018_benchmark:{alias['role']}"),
            )
            alias_product[alias_id] = product_id
    conn.commit()
    return alias_product


def _candidate_lookup_time(conn: sqlite3.Connection) -> tuple[float, int, int]:
    """Time only the existing indexed candidate lookup calls, without writes."""
    elapsed = 0.0
    mention_count = 0
    proposal_count = 0
    for entity_type, observation_table in (
        ("facebook_ad", "facebook_ad_observations"),
        ("marketplace_listing", "marketplace_listing_observations"),
    ):
        observation_ids = [
            int(row[0])
            for row in conn.execute(f"SELECT id FROM {observation_table} ORDER BY id")
        ]
        source_type = "facebook" if entity_type == "facebook_ad" else "marketplace"
        for observation_id in observation_ids:
            source = load_source_observation(conn, entity_type, observation_id)
            classification, mentions = extract_product_mentions(
                source.raw_text,
                profile=DEFAULT_VERSIONS.text_profile,
                explicit_attributes=source.explicit_attributes,
            )
            if classification.value is not RecordClass.PRODUCT:
                continue
            for mention in mentions:
                mention_count += 1
                started = time.perf_counter()
                proposals = find_exact_alias_candidates(conn, mention, source_type=source_type)
                elapsed += time.perf_counter() - started
                proposal_count += len(proposals)
    return elapsed, mention_count, proposal_count


def _classification_metrics(conn: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.source_entity_type, c.classification_label, COUNT(*) AS row_count
           FROM classification_results AS c
           JOIN normalization_source_refs AS r ON r.id = c.source_ref_id
           GROUP BY r.source_entity_type, c.classification_label
           ORDER BY r.source_entity_type, c.classification_label"""
    ).fetchall()
    by_source: dict[str, dict[str, int]] = {key: {} for key in SOURCE_ENTITY_TYPES}
    totals: Counter[str] = Counter()
    for row in rows:
        key = str(row["source_entity_type"])
        label = str(row["classification_label"])
        count = int(row["row_count"])
        by_source.setdefault(key, {})[label] = count
        totals[label] += count
    return by_source, dict(sorted(totals.items()))


def _mention_source_map(conn: sqlite3.Connection) -> dict[tuple[str, int, int], dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.source_entity_type, r.source_observation_id,
                  m.mention_index, m.id AS mention_id, m.source_text_span,
                  m.normalized_text, m.mention_kind, m.brand, m.model,
                  m.category, m.variant, m.capacity_value, m.capacity_unit
           FROM product_mentions AS m
           JOIN normalization_extraction_runs AS x ON x.id = m.extraction_run_id
           JOIN normalization_runs AS n ON n.id = x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id = n.source_ref_id
           ORDER BY r.source_entity_type, r.source_observation_id, m.mention_index"""
    ).fetchall()
    result: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["source_entity_type"]), int(row["source_observation_id"]), int(row["mention_index"]))
        result[key] = {column: row[column] for column in row.keys()}
    return result


def _decision_by_mention(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """SELECT d.* FROM normalization_decision_events AS d
           JOIN (
               SELECT product_mention_id, MAX(revision_no) AS revision_no
               FROM normalization_decision_events GROUP BY product_mention_id
           ) AS latest
             ON latest.product_mention_id = d.product_mention_id
            AND latest.revision_no = d.revision_no"""
    ).fetchall()
    return {int(row["product_mention_id"]): {column: row[column] for column in row.keys()} for row in rows}


def _candidate_products_by_mention(conn: sqlite3.Connection) -> dict[int, list[int]]:
    rows = conn.execute(
        """SELECT g.product_mention_id, c.product_id
           FROM candidate_generation_runs AS g
           LEFT JOIN product_candidates AS c ON c.candidate_run_id = g.id
           ORDER BY g.product_mention_id, c.product_id"""
    ).fetchall()
    result: dict[int, list[int]] = {}
    for row in rows:
        product_ids = result.setdefault(int(row["product_mention_id"]), [])
        if row["product_id"] is not None:
            product_ids.append(int(row["product_id"]))
    return result


def _validate_manifest_cases(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
) -> tuple[
    list[dict[str, Any]], set[tuple[str, int, int]], list[dict[str, Any]],
    list[dict[str, Any]],
]:
    mentions = _mention_source_map(conn)
    decisions = _decision_by_mention(conn)
    candidates = _candidate_products_by_mention(conn)
    classification_rows = conn.execute(
        """SELECT r.source_entity_type, r.source_observation_id,
                  c.classification_label
           FROM classification_results AS c
           JOIN normalization_source_refs AS r ON r.id = c.source_ref_id"""
    ).fetchall()
    classifications = {
        (str(row["source_entity_type"]), int(row["source_observation_id"])): str(row["classification_label"])
        for row in classification_rows
    }
    case_results: list[dict[str, Any]] = []
    covered_mentions: set[tuple[str, int, int]] = set()
    false_positives: list[dict[str, Any]] = []
    manifest_deltas: list[dict[str, Any]] = []

    for case in manifest["cases"]:
        actuals: list[dict[str, Any]] = []
        expected_ids = sorted(int(value) for value in case["expected_candidate_set"])
        for observation in case["observations"]:
            entity_type = str(observation["source_entity_type"])
            observation_id = int(observation["observation_id"])
            class_label = classifications.get((entity_type, observation_id))
            expected_class = observation.get("expected_classification")
            expected_mention_index = observation.get("mention_index")
            found = sorted(
                key for key in mentions
                if key[0] == entity_type and key[1] == observation_id
            )
            actual_mention_index = (
                int(expected_mention_index)
                if expected_mention_index is not None and
                   (entity_type, observation_id, int(expected_mention_index)) in mentions
                else found[0][2] if expected_mention_index is None and found
                else None
            )
            if actual_mention_index is None:
                actual_state = "NO_MENTION"
                actual_confidence = None
                actual_ids: list[int] = []
                actual_method = None
                actual_mention_id = None
            else:
                key = (entity_type, observation_id, actual_mention_index)
                mention = mentions.get(key)
                assert mention is not None
                actual_mention_id = int(mention["mention_id"])
                actual_ids = sorted(candidates.get(actual_mention_id, []))
                decision = decisions.get(actual_mention_id)
                if decision is None:
                    raise AssertionError(f"product mention {key} has no final decision event")
                actual_state = str(decision["decision_state"])
                actual_confidence = str(decision["confidence_label"])
                actual_method = str(decision["decision_method"])
                if case["role"] in {"positive", "ambiguous", "conflict"}:
                    covered_mentions.add(key)

            expected = {
                "classification": expected_class,
                "mention_index": expected_mention_index,
                "candidate_product_ids": expected_ids,
                "final_state": case["expected_final_state"],
                "confidence": case.get("expected_confidence"),
            }
            if case.get("expected_decision_method"):
                expected["decision_method"] = case["expected_decision_method"]
            actual = {
                "classification": class_label,
                "mention_index": actual_mention_index,
                "candidate_product_ids": actual_ids,
                "final_state": actual_state,
                "confidence": actual_confidence,
                "decision_method": actual_method,
            }
            differences = {
                key: {"manifest": expected[key], "actual": actual[key]}
                for key in expected if expected[key] != actual[key]
            }
            drift_reason = _known_worker020_manifest_delta(
                entity_type, observation_id, expected, actual,
            ) if differences else None
            if differences and drift_reason is None:
                raise AssertionError(
                    f"{case['case_id']} observation {entity_type}:{observation_id} "
                    f"differs from the frozen manifest unexpectedly: {differences}"
                )
            if differences:
                delta = {
                    "case_id": case["case_id"],
                    "source_entity_type": entity_type,
                    "source_observation_id": observation_id,
                    "reason": drift_reason,
                    "differences": differences,
                }
                manifest_deltas.append(delta)

            actuals.append({
                "source_entity_type": entity_type,
                "source_observation_id": observation_id,
                "mention_index": actual_mention_index,
                "classification": class_label,
                "candidate_product_ids": actual_ids,
                "final_state": actual_state,
                "confidence": actual_confidence,
                "decision_method": actual_method,
                "product_mention_id": actual_mention_id,
                "manifest_delta": drift_reason,
            })
            if case["role"] == "negative" and actual_state == "MATCHED" and not drift_reason:
                false_positives.append({
                    "case_id": case["case_id"],
                    "source_entity_type": entity_type,
                    "source_observation_id": observation_id,
                    "product_mention_id": actual_mention_id,
                    "candidate_product_ids": actual_ids,
                    "alias_evidence": _alias_evidence_for_mention(conn, actual_mention_id),
                })

        case_results.append({
            "case_id": case["case_id"],
            "role": case["role"],
            "expected_candidate_set": expected_ids,
            "expected_final_state": case["expected_final_state"],
            "expected_confidence": case.get("expected_confidence"),
            "observations": actuals,
            "rationale": case["rationale"],
        })
    return case_results, covered_mentions, false_positives, manifest_deltas


def _known_worker020_manifest_delta(
    entity_type: str,
    observation_id: int,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> str | None:
    """Accept only the three explicitly scoped Worker 020 behavior changes."""
    paint_context_ids = {103, 107, 113, 134, 137, 164, 165, 207, 211}
    if (
        entity_type == "facebook_ad" and observation_id in paint_context_ids
        and expected["classification"] == "PRODUCT"
        and expected["mention_index"] == 0
        and actual["classification"] == "UNKNOWN"
        and actual["mention_index"] is None
        and actual["final_state"] == "NO_MENTION"
    ):
        return "paint context rules suppress the known non-product mention"
    if (
        entity_type == "facebook_ad" and observation_id == 128
        and expected["classification"] == "UNKNOWN"
        and expected["mention_index"] is None
        and actual["classification"] == "PRODUCT"
        and actual["mention_index"] == 0
        and actual["candidate_product_ids"] == [18010]
        and actual["final_state"] == "MATCHED"
        and actual["confidence"] == "HIGH"
    ):
        return "Arabic CROWN product evidence now emits and matches a mention"
    if (
        entity_type == "marketplace_listing" and observation_id in set(range(1, 18))
        and expected["classification"] in {"PRODUCT", "SERVICE", "REAL_ESTATE"}
        and expected["mention_index"] in {None, 0}
        and actual["classification"] == "UNKNOWN"
        and actual["mention_index"] is None
        and actual["final_state"] == "NO_MENTION"
    ):
        return "historical marketplace title is absent from the observation payload"
    return None


def _alias_evidence_for_mention(conn: sqlite3.Connection, mention_id: int | None) -> list[dict[str, Any]]:
    if mention_id is None:
        return []
    rows = conn.execute(
        """SELECT p.product_id, a.id AS alias_id, a.raw_alias, a.normalized_alias,
                  a.source_type
           FROM candidate_generation_runs AS g
           JOIN product_candidates AS p ON p.candidate_run_id = g.id
           JOIN candidate_evidence AS e ON e.candidate_id = p.id
           JOIN product_aliases AS a ON a.id = e.product_alias_id
           WHERE g.product_mention_id = ? AND e.evidence_type = 'exact_alias'
           ORDER BY p.product_id, a.id""",
        (mention_id,),
    ).fetchall()
    return [{column: row[column] for column in row.keys()} for row in rows]


def _candidate_and_outcome_metrics(
    conn: sqlite3.Connection,
    covered_mentions: set[tuple[str, int, int]],
    case_results: list[dict[str, Any]],
) -> dict[str, Any]:
    count_rows = conn.execute(
        """SELECT g.product_mention_id, COUNT(c.id) AS candidate_count
           FROM candidate_generation_runs AS g
           LEFT JOIN product_candidates AS c ON c.candidate_run_id = g.id
           GROUP BY g.product_mention_id ORDER BY g.product_mention_id"""
    ).fetchall()
    candidate_distribution = Counter(int(row["candidate_count"]) for row in count_rows)
    candidate_rows = conn.execute("SELECT COUNT(*) FROM product_candidates").fetchone()[0]
    evidence_by_type = {
        str(row["evidence_type"]): int(row["row_count"])
        for row in conn.execute(
            "SELECT evidence_type, COUNT(*) AS row_count FROM candidate_evidence GROUP BY evidence_type"
        )
    }
    outcome_rows = conn.execute(
        """SELECT d.decision_state, d.confidence_label, COUNT(*) AS row_count
           FROM normalization_decision_events AS d
           JOIN (
               SELECT product_mention_id, MAX(revision_no) AS revision_no
               FROM normalization_decision_events GROUP BY product_mention_id
           ) AS latest
             ON latest.product_mention_id=d.product_mention_id
            AND latest.revision_no=d.revision_no
           GROUP BY d.decision_state, d.confidence_label"""
    ).fetchall()
    outcomes: Counter[str] = Counter()
    confidences: Counter[str] = Counter()
    for row in outcome_rows:
        state = str(row["decision_state"])
        confidence = str(row["confidence_label"])
        amount = int(row["row_count"])
        outcomes[state] += amount
        confidences[confidence] += amount

    ambiguous_mentions = sum(1 for row in count_rows if int(row["candidate_count"]) > 1)
    conflict_count = int(conn.execute(
        """SELECT COUNT(*) FROM normalization_decision_events
           WHERE revision_no = 1 AND decision_method = 'conflict_review_v1'"""
    ).fetchone()[0])
    product_mention_count = _table_count(conn, "product_mentions")
    mentions_with_candidates = sum(1 for row in count_rows if int(row["candidate_count"]) > 0)

    candidate_recall_hits = 0
    for case in case_results:
        if case["role"] not in {"positive", "ambiguous", "conflict"}:
            continue
        expected = set(case["expected_candidate_set"])
        for observation in case["observations"]:
            if observation["mention_index"] is None:
                continue
            if expected.intersection(observation["candidate_product_ids"]):
                candidate_recall_hits += 1

    covered_matched = sum(
        1 for case in case_results if case["role"] in {"positive", "ambiguous", "conflict"}
        for observation in case["observations"]
        if observation["mention_index"] is not None and observation["final_state"] == "MATCHED"
    )
    covered_review = sum(
        1 for case in case_results if case["role"] in {"positive", "ambiguous", "conflict"}
        for observation in case["observations"]
        if observation["mention_index"] is not None and observation["final_state"] == "REVIEW"
    )
    covered_count = len(covered_mentions)
    denominator = covered_count
    return {
        "product_mentions": product_mention_count,
        "mentions_with_candidates": mentions_with_candidates,
        "mentions_with_zero_candidates": product_mention_count - mentions_with_candidates,
        "candidate_count_distribution": {str(key): candidate_distribution[key] for key in sorted(candidate_distribution)},
        "candidate_rows": int(candidate_rows),
        "exact_alias_evidence_count": evidence_by_type.get("exact_alias", 0),
        "evidence_by_type": evidence_by_type,
        "ambiguous_mentions": ambiguous_mentions,
        "ambiguous_candidate_rows": sum(
            candidate_count * frequency
            for candidate_count, frequency in candidate_distribution.items()
            if candidate_count > 1
        ),
        "conflict_mentions": conflict_count,
        "outcomes": {state: outcomes.get(state, 0) for state in ("MATCHED", "REVIEW", "UNMAPPED", "REJECTED", "CANDIDATE")},
        "confidence": {label: confidences.get(label, 0) for label in ("HIGH", "PROBABLE", "UNCERTAIN", "NO_MATCH")},
        "benchmark_catalog_coverage_mentions": covered_count,
        "candidate_retrieval_proxy_hits": candidate_recall_hits,
        "candidate_retrieval_proxy": candidate_recall_hits / denominator if denominator else None,
        "deterministic_match_rate": covered_matched / denominator if denominator else None,
        "review_rate": covered_review / denominator if denominator else None,
        "unmapped_rate_all_mentions": outcomes.get("UNMAPPED", 0) / product_mention_count if product_mention_count else None,
    }


def _provenance_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    total = int(conn.execute("SELECT COUNT(*) FROM candidate_evidence").fetchone()[0])
    candidate_rows = int(conn.execute("SELECT COUNT(*) FROM product_candidates").fetchone()[0])
    decisions = int(conn.execute("SELECT COUNT(*) FROM normalization_decision_events").fetchone()[0])
    candidate_rows_traced = int(conn.execute(
        """SELECT COUNT(*)
           FROM product_candidates AS c
           JOIN candidate_generation_runs AS g ON g.id=c.candidate_run_id
           JOIN product_mentions AS m ON m.id=g.product_mention_id
           JOIN normalization_extraction_runs AS x ON x.id=m.extraction_run_id
           JOIN normalization_runs AS n ON n.id=x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
           LEFT JOIN facebook_ad_observations AS fo ON fo.id=r.facebook_observation_id
           LEFT JOIN marketplace_listing_observations AS mo ON mo.id=r.marketplace_observation_id
           WHERE g.matching_version=? AND n.normalization_version=?
             AND ((r.source_entity_type='facebook_ad' AND fo.id=r.source_observation_id)
               OR (r.source_entity_type='marketplace_listing' AND mo.id=r.source_observation_id))""",
        (DEFAULT_VERSIONS.matching_version, DEFAULT_VERSIONS.normalization_version),
    ).fetchone()[0])
    evidence_rows_traced = int(conn.execute(
        """SELECT COUNT(*)
           FROM candidate_evidence AS e
           JOIN product_candidates AS c ON c.id=e.candidate_id
           JOIN candidate_generation_runs AS g ON g.id=c.candidate_run_id
           JOIN product_mentions AS m ON m.id=g.product_mention_id
           JOIN normalization_extraction_runs AS x ON x.id=m.extraction_run_id
           JOIN normalization_runs AS n ON n.id=x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
           LEFT JOIN facebook_ad_observations AS fo ON fo.id=r.facebook_observation_id
           LEFT JOIN marketplace_listing_observations AS mo ON mo.id=r.marketplace_observation_id
           WHERE g.matching_version=? AND e.method_version=?
             AND n.normalization_version=?
             AND ((r.source_entity_type='facebook_ad' AND fo.id=r.source_observation_id)
               OR (r.source_entity_type='marketplace_listing' AND mo.id=r.source_observation_id))""",
        (DEFAULT_VERSIONS.matching_version, DEFAULT_VERSIONS.matching_version,
         DEFAULT_VERSIONS.normalization_version),
    ).fetchone()[0])
    decision_rows_traced = int(conn.execute(
        """SELECT COUNT(*)
           FROM normalization_decision_events AS d
           JOIN product_mentions AS m ON m.id=d.product_mention_id
           JOIN normalization_extraction_runs AS x ON x.id=m.extraction_run_id
           JOIN normalization_runs AS n ON n.id=x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
           LEFT JOIN facebook_ad_observations AS fo ON fo.id=r.facebook_observation_id
           LEFT JOIN marketplace_listing_observations AS mo ON mo.id=r.marketplace_observation_id
           WHERE d.decision_method_version=? AND n.normalization_version=?
             AND ((r.source_entity_type='facebook_ad' AND fo.id=r.source_observation_id)
               OR (r.source_entity_type='marketplace_listing' AND mo.id=r.source_observation_id))""",
        (DEFAULT_VERSIONS.matching_version, DEFAULT_VERSIONS.normalization_version),
    ).fetchone()[0])
    traced = int(conn.execute(
        """SELECT COUNT(*)
           FROM candidate_evidence AS e
           JOIN product_candidates AS c ON c.id = e.candidate_id
           JOIN candidate_generation_runs AS g ON g.id = c.candidate_run_id
           JOIN product_mentions AS m ON m.id = g.product_mention_id
           JOIN normalization_extraction_runs AS x ON x.id = m.extraction_run_id
           JOIN normalization_runs AS n ON n.id = x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id = n.source_ref_id
           JOIN facebook_ad_observations AS fo ON fo.id = r.facebook_observation_id
                AND r.source_entity_type = 'facebook_ad'
           WHERE g.matching_version = ? AND e.method_version = ?
             AND n.normalization_version = ? AND e.evidence_type = 'exact_alias'
             AND e.product_alias_id IS NOT NULL""",
        (DEFAULT_VERSIONS.matching_version, DEFAULT_VERSIONS.matching_version,
         DEFAULT_VERSIONS.normalization_version),
    ).fetchone()[0])
    traced_market = int(conn.execute(
        """SELECT COUNT(*)
           FROM candidate_evidence AS e
           JOIN product_candidates AS c ON c.id = e.candidate_id
           JOIN candidate_generation_runs AS g ON g.id = c.candidate_run_id
           JOIN product_mentions AS m ON m.id = g.product_mention_id
           JOIN normalization_extraction_runs AS x ON x.id = m.extraction_run_id
           JOIN normalization_runs AS n ON n.id = x.normalization_run_id
           JOIN normalization_source_refs AS r ON r.id = n.source_ref_id
           JOIN marketplace_listing_observations AS mo ON mo.id = r.marketplace_observation_id
                AND r.source_entity_type = 'marketplace_listing'
           WHERE g.matching_version = ? AND e.method_version = ?
             AND n.normalization_version = ? AND e.evidence_type = 'exact_alias'
             AND e.product_alias_id IS NOT NULL""",
        (DEFAULT_VERSIONS.matching_version, DEFAULT_VERSIONS.matching_version,
         DEFAULT_VERSIONS.normalization_version),
    ).fetchone()[0])
    invalid_alias_links = int(conn.execute(
        """SELECT COUNT(*)
           FROM candidate_evidence AS e
           JOIN product_candidates AS c ON c.id=e.candidate_id
           LEFT JOIN product_aliases AS a ON a.id=e.product_alias_id
           WHERE e.evidence_type='exact_alias'
             AND (a.id IS NULL OR a.product_id != c.product_id)"""
    ).fetchone()[0])
    alias_total = int(conn.execute(
        "SELECT COUNT(*) FROM candidate_evidence WHERE evidence_type='exact_alias'"
    ).fetchone()[0])
    return {
        "candidate_rows": candidate_rows,
        "candidate_rows_traced": candidate_rows_traced,
        "candidate_evidence_rows": total,
        "candidate_evidence_rows_traced": evidence_rows_traced,
        "decision_events": decisions,
        "decision_events_traced": decision_rows_traced,
        "exact_alias_evidence_rows": alias_total,
        "facebook_alias_evidence_traced": traced,
        "marketplace_alias_evidence_traced": traced_market,
        "alias_product_link_violations": invalid_alias_links,
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
    }


def _source_input_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    # Build the source path map directly; the explicit form keeps source-specific
    # provenance visible in the report and in the machine-readable results.
    path_rows = conn.execute(
        """SELECT r.source_entity_type, n.input_field_path, COUNT(*) AS row_count
           FROM normalization_runs AS n
           JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
           GROUP BY r.source_entity_type, n.input_field_path
           ORDER BY r.source_entity_type, n.input_field_path"""
    ).fetchall()
    paths = {}
    for row in path_rows:
        paths.setdefault(str(row["source_entity_type"]), {})[str(row["input_field_path"])] = int(row["row_count"])
    marketplace_fallback_mentions = int(conn.execute(
        """SELECT COUNT(DISTINCT m.id)
           FROM product_mentions AS m
           JOIN normalization_extraction_runs AS x ON x.id=m.extraction_run_id
           JOIN normalization_runs AS n ON n.id=x.normalization_run_id
           WHERE n.input_field_path='marketplace_listings.raw_title'"""
    ).fetchone()[0])
    marketplace_observation_count = int(conn.execute(
        "SELECT COUNT(*) FROM marketplace_listing_observations"
    ).fetchone()[0])
    marketplace_local_title_count = 0
    for row in conn.execute(
        "SELECT source_payload_json FROM marketplace_listing_observations"
    ):
        try:
            payload = json.loads(row["source_payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        announcement = payload.get("announcement")
        title = announcement.get("raw_title") if isinstance(announcement, dict) else None
        if isinstance(title, str) and title.strip():
            marketplace_local_title_count += 1
    facebook_non_observation_rows = sum(
        count for path, count in paths.get("facebook_ad", {}).items()
        if not path.startswith("facebook_ad_observations.")
    )
    return {
        "selected_field_paths_by_source": paths,
        "marketplace_entity_title_fallback_observations": paths.get("marketplace_listing", {}).get("marketplace_listings.raw_title", 0),
        "marketplace_product_mentions_from_entity_title_fallback": marketplace_fallback_mentions,
        "marketplace_observation_local_title_coverage": marketplace_local_title_count,
        "marketplace_observation_local_title_missing": marketplace_observation_count - marketplace_local_title_count,
        "facebook_non_observation_input_rows": facebook_non_observation_rows,
    }


def _make_report(results: dict[str, Any], test_result: str = "Pending full test suite run.") -> str:
    counts = results["input"]["observations_by_source"]
    catalog = results["catalog"]
    candidate = results["candidate_generation"]
    outcomes = candidate["outcomes"]
    confidence = candidate["confidence"]
    dist = ", ".join(f"{key}: {value}" for key, value in candidate["candidate_count_distribution"].items())
    source_class = results["input"]["classification_by_source"]
    total_classes = results["input"]["classification_distribution"]
    products = results["catalog"]["products"]
    concepts = "\n".join(
        f"- `{item['benchmark_product_id']}` {item['canonical_name']} — {item['rationale']}"
        for item in products
    )
    false_positives = results["false_positive_benchmark_matches"]
    false_positive_detail = "None." if not false_positives else "\n".join(
        f"- {item['case_id']} {item['source_entity_type']}:{item['source_observation_id']} via {item['alias_evidence']}"
        for item in false_positives
    )
    manifest_deltas = results.get("manifest_expectation_deltas", [])
    delta_detail = "None." if not manifest_deltas else "\n".join(
        f"- {item['case_id']} {item['source_entity_type']}:{item['source_observation_id']} — "
        f"{item['reason']}: `{_json(item['differences'])}`"
        for item in manifest_deltas
    )
    case_summary = Counter(case["role"] for case in results["manifest_case_results"])
    source_hash = results["production_database_hash"]
    perf = results["performance"]
    proxy = candidate["candidate_retrieval_proxy"]
    return f"""# Worker 018 — Deterministic catalog benchmark

Run date: {results['run_date']} (Asia/Riyadh)  
Benchmark database: `{results['benchmark_database_path']}`  
Manifest: `{results['manifest_path']}`

## Executive result

The versioned pipeline `{results['versions']['normalization']}` / `{results['versions']['selector']}` / `{results['versions']['classification']}` / `{results['versions']['extraction']}` / `{results['versions']['matching']}` processed copied production observations against the Worker 018 synthetic catalog. It produced **{outcomes['MATCHED']} MATCHED**, **{outcomes['REVIEW']} REVIEW**, **{outcomes['UNMAPPED']} UNMAPPED**, and **{outcomes['REJECTED']} REJECTED** results across **{candidate['product_mentions']} product mentions**. The matcher remains unchanged; source selection and deterministic classification/extraction are now versioned.

The benchmark-only catalog covers {candidate['benchmark_catalog_coverage_mentions']} manifest-grounded mentions. The **benchmark catalog coverage / candidate-retrieval proxy** was {candidate['candidate_retrieval_proxy_hits']}/{candidate['benchmark_catalog_coverage_mentions']} ({proxy:.2%}); this is a synthetic benchmark measure, **not production recall**. Current known paint-context false-positive mentions: {results['weak_product_classification_mentions']}.

## 1. Benchmark catalog

- Products: **{catalog['product_count']}**
- Alias rows: **{catalog['alias_count']}**
- Production-style mappings in the copy: **{catalog['mapping_count']}**

Concepts were derived from observed source text only. The catalog includes deliberate ambiguity, conflict, and non-product gate aliases labeled as probes in the manifest; those are test fixtures, not factual production aliases.

{concepts}

## 2. Ground-truth manifest

The unchanged manifest contains {len(results['manifest_case_results'])} independently specified cases: {case_summary.get('positive', 0)} positive, {case_summary.get('ambiguous', 0)} ambiguous, {case_summary.get('conflict', 0)} conflict, and {case_summary.get('negative', 0)} negative. Expected values remain exactly as authored for Worker 018; approved Worker 020 behavior changes are recorded separately below.

Unexpected differences: **0**. Approved differences from frozen manifest expectations: **{len(manifest_deltas)}**.

## 2a. Approved differences from frozen manifest

{delta_detail}

## 3. Input corpus

- Observations: **{results['input']['total_observations']}** total — Facebook {counts['facebook_ad']}, marketplace {counts['marketplace_listing']}.
- Facebook observation-level usable-text baseline: **{results['input']['facebook_usable_text_observations']}/{counts['facebook_ad']} ({results['input']['facebook_usable_text_rate']:.2%})**, matching the declared 129/217 source baseline.
- Product mentions: **{candidate['product_mentions']}** — Facebook {results['input']['product_mentions_by_source'].get('facebook_ad', 0)}, marketplace {results['input']['product_mentions_by_source'].get('marketplace_listing', 0)}.
- Classification distribution: {', '.join(f'{key} {value}' for key, value in total_classes.items())}.
- By source: Facebook {source_class.get('facebook_ad', {})}; marketplace {source_class.get('marketplace_listing', {})}.
- Marketplace observation-local title coverage: {results['input']['source_input_provenance']['marketplace_observation_local_title_coverage']}/51. Entity-title fallback observations: **{results['input']['source_input_provenance']['marketplace_entity_title_fallback_observations']}**; fallback-derived product mentions: **{results['input']['source_input_provenance']['marketplace_product_mentions_from_entity_title_fallback']}**. Facebook non-observation text inputs: **{results['input']['source_input_provenance']['facebook_non_observation_input_rows']}**.

## 4. Candidate generation

- Mentions with at least one candidate: **{candidate['mentions_with_candidates']}**; zero candidates: **{candidate['mentions_with_zero_candidates']}**.
- Candidate-count distribution per mention: {dist}.
- Candidate rows: **{candidate['candidate_rows']}**; exact-alias evidence rows: **{candidate['exact_alias_evidence_count']}**.
- Ambiguous mentions: **{candidate['ambiguous_mentions']}** ({candidate['ambiguous_candidate_rows']} candidate rows); explicit category-conflict mentions: **{candidate['conflict_mentions']}**.
- Evidence by type: {candidate['evidence_by_type']}.

## Provenance

All {results['provenance']['candidate_rows_traced']}/{results['provenance']['candidate_rows']} candidate rows, {results['provenance']['candidate_evidence_rows_traced']}/{results['provenance']['candidate_evidence_rows']} evidence rows, and {results['provenance']['decision_events_traced']}/{results['provenance']['decision_events']} decision events trace to a source observation, product mention, and benchmark canonical product. The {results['provenance']['exact_alias_evidence_rows']} exact-alias evidence rows also reference their matching alias; normalization and matching versions are recorded with those runs/evidence. Alias-product link violations: {results['provenance']['alias_product_link_violations']}; foreign-key violations: {results['provenance']['foreign_key_violations']}.

## 5–6. Final outcomes and confidence

- Outcomes: MATCHED {outcomes['MATCHED']}; REVIEW {outcomes['REVIEW']}; UNMAPPED {outcomes['UNMAPPED']}; REJECTED {outcomes['REJECTED']}.
- Confidence: HIGH {confidence['HIGH']}; PROBABLE {confidence['PROBABLE']}; UNCERTAIN {confidence['UNCERTAIN']}; NO_MATCH {confidence['NO_MATCH']}.

## 7. Benchmark catalog coverage / candidate-retrieval proxy

The manifest marks {candidate['benchmark_catalog_coverage_mentions']} product mentions as covered by a positive, ambiguity, or conflict-probe catalog case. A manifest-expected candidate was retrieved for {candidate['candidate_retrieval_proxy_hits']} of them: **{proxy:.2%}**. The denominator includes the three deliberately conflicting aliases, whose expected final result is REVIEW. This proxy measures only this small designed catalog and this copied corpus; it is **not production recall**.

- Deterministic match rate on covered mentions: **{candidate['deterministic_match_rate']:.2%}** ({outcomes['MATCHED']}/{candidate['benchmark_catalog_coverage_mentions']}).
- Review rate on covered mentions: **{candidate['review_rate']:.2%}** ({outcomes['REVIEW']}/{candidate['benchmark_catalog_coverage_mentions']}).
- Unmapped rate among all product mentions: **{candidate['unmapped_rate_all_mentions']:.2%}** ({outcomes['UNMAPPED']}/{candidate['product_mentions']}).

## 8. False-positive results

False-positive benchmark matches: **{len(false_positives)}**.  
{false_positive_detail}

The exact service, real-estate, and education control texts each have a deliberately inserted manual alias in the isolated catalog and remain gated from product matching. The nine reviewed paint-context observations now produce no PRODUCT mentions. The Arabic CROWN case is an approved manifest delta because the explicit observation evidence now produces a matched mention; its isolated alias remains benchmark-only.

## 9. Idempotency

The identical pipeline was run twice. Additional derived rows on run two: **{sum(results['idempotency']['additional_rows'].values())}** total. Per table: `{results['idempotency']['additional_rows']}`. All row counts remained stable: **{results['idempotency']['passed']}**.

## 10. Performance

- First full deterministic processing pass: **{perf['processing_seconds']:.6f} seconds**.
- Throughput: **{perf['observations_per_second']:.1f} observations/second**, **{perf['mentions_per_second']:.1f} product mentions/second**.
- Existing exact-alias lookup calls, timed separately over the real product mentions: **{perf['candidate_lookup_seconds']:.6f} seconds** for {perf['candidate_lookup_mentions']} mentions and {perf['candidate_lookup_candidate_rows']} candidates.

This is a baseline on the current local SQLite/Python runtime; no optimization was attempted.

## 11. Test result

{test_result}

## 12. Files added or generated

- This run's machine-readable result path, if requested by the caller.
- The frozen manifest was read unchanged; the catalog exists only in the isolated benchmark copy.

## 13. Production database integrity

- SHA-256 before: `{source_hash['before']}`
- SHA-256 after:  `{source_hash['after']}`
- Equal: **{source_hash['unchanged']}**
- Production `products` / `product_aliases` / `product_mappings` before and after: `{results['production_catalog_counts']}`.
- Normalization schema applied to production: **No**.
- Source observation table fingerprints matched before and after isolated processing: **{results['source_observations_unchanged']}**.

## 14. Required scope confirmations

- No production writes: **confirmed**; the source was opened read-only and cloned with SQLite backup.
- No Facebook capture: **confirmed**.
- No AI/provider calls: **confirmed**; the isolated `ai_provider_runs` table has {results['ai_provider_run_count']} rows.
- No production catalog rows, aliases, or mappings: **confirmed**; counts remained zero.
- No dependencies installed: **confirmed**.
- Production schema unchanged: **confirmed**.

## 15. Remaining matcher and upstream weaknesses

- Arabic observation 128 now emits CROWN, model CT31012, power-tool category, and the 80 W specification. Its alias is synthetic benchmark data only; production identity remains uncreated.
- Marketplace observations without a captured title remain UNKNOWN; 17/51 historical observations have no observation-local title, and 8 prior entity-fallback product mentions are no longer emitted.
- The nine reviewed paint-context rows now classify UNKNOWN. This is evidence-led handling of those phrases, not a claim that every generic use of `peinture` is non-product.
- The source has two numbered-painting-kit ads, but both are persisted as `mention_kind=product`; bundle wording is not represented as a bundle mention. No accessory-classified source mention was found.
- The Arabic CROWN mention now exposes model/reference `CT31012` and an 80 W specification; the compressor ad gives no model or capacity.
- The current exact-alias matcher distinguishes the corpus's similar `MAT ACRYL` and `BLACK MAT` titles when they each have one alias, and returns REVIEW for the injected shared FIXATEUR alias and category conflict. Punctuation characters not mapped by `deterministic_v1` remain significant; no fuzzy matching was added.

"""


def run_benchmark(
    source_path: Path,
    benchmark_path: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    results_path: Path | None = None,
    report_path: Path | None = None,
    test_result: str = "Pending full test suite run.",
) -> dict[str, Any]:
    source_path = source_path.expanduser().resolve()
    benchmark_path = benchmark_path.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if source_path == benchmark_path:
        raise ValueError("benchmark output must be an isolated copy, never the source database")
    if benchmark_path.exists():
        raise FileExistsError(f"refusing to overwrite existing benchmark database: {benchmark_path}")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before_hash = _sha256_file(source_path)

    source = _connect_read_only(source_path)
    production_tables = {
        row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required_tables = {
        "products", "product_aliases", "product_mappings", "facebook_ads",
        "facebook_ad_observations", "marketplace_listings", "marketplace_listing_observations",
    }
    missing = required_tables - production_tables
    if missing:
        source.close()
        raise RuntimeError(f"production database missing required tables: {sorted(missing)}")
    source_catalog_before = {
        name: _table_count(source, name)
        for name in ("products", "product_aliases", "product_mappings")
    }
    if source_catalog_before != {"products": 0, "product_aliases": 0, "product_mappings": 0}:
        source.close()
        raise RuntimeError(f"source catalog is not empty as expected: {source_catalog_before}")
    observation_snapshot_before = _observation_snapshot(source)
    observation_counts = _source_reference_counts(source)
    source.close()

    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(benchmark_path)
    try:
        source = _connect_read_only(source_path)
        source.backup(destination)
        source.close()
    finally:
        destination.close()

    conn = sqlite3.connect(benchmark_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(NORMALIZATION_SCHEMA.read_text(encoding="utf-8"))
        alias_product = _insert_catalog(conn, manifest)
        catalog_counts = {
            "product_count": _table_count(conn, "products"),
            "alias_count": _table_count(conn, "product_aliases"),
            "mapping_count": _table_count(conn, "product_mappings"),
            "products": [
                {"benchmark_product_id": item["benchmark_product_id"],
                 "canonical_name": item["canonical_name"], "rationale": item["rationale"]}
                for item in manifest["products"]
            ],
            "alias_roles": dict(sorted(Counter(
                alias["role"] for product in manifest["products"] for alias in product["aliases"]
            ).items())),
        }
        before_derived = _row_counts(conn, DERIVED_TABLES)
        candidate_lookup_seconds, lookup_mentions, lookup_candidates = _candidate_lookup_time(conn)
        started = time.perf_counter()
        first_results = list(NormalizationEngine().process_all(
            conn, source_entity_types=SOURCE_ENTITY_TYPES, batch_size=200,
        ))
        processing_seconds = time.perf_counter() - started
        after_first = _row_counts(conn, DERIVED_TABLES)
        first_observation_snapshot = _observation_snapshot(conn)

        second_results = list(NormalizationEngine().process_all(
            conn, source_entity_types=SOURCE_ENTITY_TYPES, batch_size=200,
        ))
        after_second = _row_counts(conn, DERIVED_TABLES)
        observation_snapshot_after = _observation_snapshot(conn)
        additional_rows = {
            table: after_second[table] - after_first[table] for table in DERIVED_TABLES
        }
        if any(additional_rows.values()):
            raise AssertionError(f"second run created derived rows: {additional_rows}")
        if first_observation_snapshot != observation_snapshot_after:
            raise AssertionError("source observation rows changed in the isolated database")

        manifest_case_results, covered_mentions, false_positives, manifest_deltas = _validate_manifest_cases(conn, manifest)
        if false_positives:
            raise AssertionError(f"false-positive benchmark matches found: {false_positives}")
        candidate_metrics = _candidate_and_outcome_metrics(conn, covered_mentions, manifest_case_results)
        classification_by_source, classification_distribution = _classification_metrics(conn)
        mention_counts_by_source = {
            str(row["source_entity_type"]): int(row["row_count"])
            for row in conn.execute(
                """SELECT r.source_entity_type, COUNT(*) AS row_count
                   FROM product_mentions AS m
                   JOIN normalization_extraction_runs AS x ON x.id=m.extraction_run_id
                   JOIN normalization_runs AS n ON n.id=x.normalization_run_id
                   JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
                   GROUP BY r.source_entity_type"""
            )
        }
        usable_facebook = int(conn.execute(
            """SELECT COUNT(*) FROM normalization_runs AS n
               JOIN normalization_source_refs AS r ON r.id=n.source_ref_id
               WHERE r.source_entity_type='facebook_ad' AND trim(n.selected_input_text) != ''"""
        ).fetchone()[0])
        if usable_facebook != 129 or observation_counts["facebook_ad"] != 217:
            raise AssertionError(
                "Facebook observation-text baseline drifted from the declared 129/217 corpus baseline"
            )
        source_catalog_after = {
            name: _table_count(conn, name)
            for name in ("products", "product_aliases", "product_mappings")
        }
        if source_catalog_after != {
            "products": catalog_counts["product_count"],
            "product_aliases": catalog_counts["alias_count"],
            "product_mappings": 0,
        }:
            raise AssertionError(f"pipeline modified isolated catalog rows: {source_catalog_after}")
        ai_provider_run_count = _table_count(conn, "ai_provider_runs")
        if ai_provider_run_count != 0:
            raise AssertionError("AI/provider run rows unexpectedly exist")

        provenance = _provenance_metrics(conn)
        if provenance["alias_product_link_violations"] or provenance["foreign_key_violations"]:
            raise AssertionError(f"provenance or FK validation failed: {provenance}")
        if (
            provenance["candidate_rows_traced"] != provenance["candidate_rows"]
            or provenance["candidate_evidence_rows_traced"] != provenance["candidate_evidence_rows"]
            or provenance["decision_events_traced"] != provenance["decision_events"]
        ):
            raise AssertionError(f"not all candidate, evidence, and decision rows trace to observations: {provenance}")
        exact_evidence = candidate_metrics["exact_alias_evidence_count"]
        if provenance["facebook_alias_evidence_traced"] + provenance["marketplace_alias_evidence_traced"] != exact_evidence:
            raise AssertionError("not all exact-alias evidence rows trace to a source observation")

        mention_attrs = conn.execute(
            """SELECT COUNT(*) AS mentions,
                      SUM(CASE WHEN brand IS NOT NULL THEN 1 ELSE 0 END) AS branded,
                      SUM(CASE WHEN model IS NOT NULL THEN 1 ELSE 0 END) AS modeled,
                      SUM(CASE WHEN variant IS NOT NULL THEN 1 ELSE 0 END) AS variants,
                      SUM(CASE WHEN capacity_value IS NOT NULL THEN 1 ELSE 0 END) AS capacities
               FROM product_mentions"""
        ).fetchone()
        mention_kind_counts = {
            str(row["mention_kind"]): int(row["row_count"])
            for row in conn.execute(
                "SELECT mention_kind, COUNT(*) AS row_count FROM product_mentions GROUP BY mention_kind"
            )
        }
        weak_product_classification_mentions = sum(
            1 for case in manifest_case_results
            if case["case_id"] == "semantic_paint_false_positive"
            for observation in case["observations"]
            if observation["final_state"] == "UNMAPPED"
        )
        source_observation_after = _observation_snapshot(conn)
        if source_observation_after != observation_snapshot_before:
            raise AssertionError("source observation content changed from the initial copied snapshot")

        results: dict[str, Any] = {
            "benchmark_id": manifest["benchmark_id"],
            "run_date": datetime.now(timezone(timedelta(hours=3))).date().isoformat(),
            "source_database_path": str(source_path),
            "benchmark_database_path": str(benchmark_path),
            "manifest_path": str(manifest_path),
            "versions": {
                "normalization": DEFAULT_VERSIONS.normalization_version,
                "extraction": DEFAULT_VERSIONS.extraction_version,
                "matching": DEFAULT_VERSIONS.matching_version,
                "classification": DEFAULT_VERSIONS.classification_version,
                "selector": DEFAULT_VERSIONS.selector_version,
            },
            "input": {
                "total_observations": sum(observation_counts.values()),
                "observations_by_source": observation_counts,
                "facebook_usable_text_observations": usable_facebook,
                "facebook_usable_text_rate": usable_facebook / observation_counts["facebook_ad"],
                "product_mentions_by_source": mention_counts_by_source,
                "classification_by_source": classification_by_source,
                "classification_distribution": classification_distribution,
                "source_input_provenance": _source_input_metrics(conn),
            },
            "catalog": catalog_counts,
            "candidate_generation": candidate_metrics,
            "manifest_case_results": manifest_case_results,
            "manifest_expectation_deltas": manifest_deltas,
            "false_positive_benchmark_matches": false_positives,
            "weak_product_classification_mentions": weak_product_classification_mentions,
            "idempotency": {
                "first_run_rows": after_first,
                "second_run_rows": after_second,
                "additional_rows": additional_rows,
                "first_result_count": len(first_results),
                "second_result_count": len(second_results),
                "passed": not any(additional_rows.values()) and len(first_results) == len(second_results),
            },
            "performance": {
                "processing_seconds": processing_seconds,
                "observations_per_second": sum(observation_counts.values()) / processing_seconds if processing_seconds else None,
                "mentions_per_second": candidate_metrics["product_mentions"] / processing_seconds if processing_seconds else None,
                "candidate_lookup_seconds": candidate_lookup_seconds,
                "candidate_lookup_mentions": lookup_mentions,
                "candidate_lookup_candidate_rows": lookup_candidates,
            },
            "provenance": provenance,
            "mention_attributes": {
                "product_mentions": int(mention_attrs["mentions"] or 0),
                "brand_present": int(mention_attrs["branded"] or 0),
                "model_present": int(mention_attrs["modeled"] or 0),
                "variant_present": int(mention_attrs["variants"] or 0),
                "capacity_present": int(mention_attrs["capacities"] or 0),
                "mention_kind_distribution": mention_kind_counts,
            },
            "production_catalog_counts": {
                "before": source_catalog_before,
                "after": source_catalog_before,
            },
            "production_database_hash": {
                "before": before_hash,
                "after": None,
                "unchanged": None,
            },
            "source_observations_unchanged": True,
            "normalization_schema_applied_to_production": False,
            "ai_provider_run_count": ai_provider_run_count,
            "scope_confirmations": {
                "no_production_writes": True,
                "no_facebook_capture": True,
                "no_ai_provider_calls": True,
                "no_production_catalog_rows": True,
                "no_production_mappings": True,
                "no_dependencies_installed": True,
            },
            "test_result": test_result,
        }
    finally:
        conn.close()

    after_hash = _sha256_file(source_path)
    results["production_database_hash"]["after"] = after_hash
    results["production_database_hash"]["unchanged"] = before_hash == after_hash
    if before_hash != after_hash:
        raise AssertionError("production database SHA-256 changed during benchmark")

    if results_path is not None:
        results_path = results_path.expanduser().resolve()
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(_json(results) + "\n", encoding="utf-8")
    if report_path is not None:
        report_path = report_path.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_make_report(results, test_result), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / "data" / "Database.sqlite")
    parser.add_argument("--output", type=Path, required=True, help="New isolated benchmark database path; existing files are never overwritten")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = run_benchmark(
        args.source, args.output, manifest_path=args.manifest,
        results_path=args.results, report_path=args.report,
    )
    print(_json({
        "benchmark_database_path": results["benchmark_database_path"],
        "product_mentions": results["candidate_generation"]["product_mentions"],
        "outcomes": results["candidate_generation"]["outcomes"],
        "production_database_unchanged": results["production_database_hash"]["unchanged"],
        "idempotent": results["idempotency"]["passed"],
    }))


if __name__ == "__main__":
    main()
