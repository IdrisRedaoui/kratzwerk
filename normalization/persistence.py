"""SQLite persistence for the Normalization V1 schema and classification results."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Mapping

from .extract import (
    Classification,
    ProductMention,
    RecordClass,
    TextSelection,
    extract_product_mentions,
    select_facebook_text,
    select_ouedkniss_text,
)
from .match import (
    CandidateProposal,
    MatchEvaluation,
    candidate_input_hash,
    catalog_snapshot_hash,
    evaluate_candidates,
    find_exact_alias_candidates,
)
from .text import NormalizationProfile, normalize_text
from .versioning import DEFAULT_VERSIONS, EngineVersions


class VersionDriftError(RuntimeError):
    """Raised when one stored version/input pair produces different output."""


class SourceObservationNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    source_platform: str
    source_entity_type: str
    source_record_id: int
    source_observation_id: int
    source_ref_id: int
    normalization_run_id: int
    extraction_run_id: int
    classification_result_id: int
    classification: Classification
    mention_ids: tuple[int, ...]
    candidate_run_ids: tuple[int, ...]
    candidate_count: int
    evidence_count: int
    decision_event_ids: tuple[int, ...]
    normalization_version: str
    extraction_version: str
    matching_version: str
    classification_version: str


@dataclass(frozen=True)
class LoadedObservation:
    platform: str
    entity_type: str
    record_id: int
    observation_id: int
    field_path: str
    raw_text: str
    explicit_attributes: Mapping[str, str]
    source_span_start: int | None = None
    source_span_end: int | None = None


def load_source_observation(
    conn: sqlite3.Connection,
    source_entity_type: str,
    source_observation_id: int,
) -> LoadedObservation:
    """Read one V2 observation and select only its source-backed text evidence."""
    if source_entity_type == "facebook_ad":
        cursor = conn.execute(
            """SELECT a.platform, a.id AS record_id,
                      o.id AS observation_id, o.detected_metadata_json
               FROM facebook_ad_observations AS o
               JOIN facebook_ads AS a ON a.id = o.ad_id
               WHERE o.id = ?""",
            (source_observation_id,),
        )
        values = _row_dict(cursor, cursor.fetchone())
        if values is None:
            raise SourceObservationNotFound(
                f"No facebook_ad_observations row with id {source_observation_id}"
            )
        selection = select_facebook_text(values["detected_metadata_json"])
        return LoadedObservation(
            platform=str(values["platform"]), entity_type=source_entity_type,
            record_id=int(values["record_id"]), observation_id=int(values["observation_id"]),
            field_path=selection.field_path, raw_text=selection.raw_text,
            explicit_attributes=selection.explicit_attributes,
            source_span_start=selection.source_span_start,
            source_span_end=selection.source_span_end,
        )
    if source_entity_type == "marketplace_listing":
        cursor = conn.execute(
            """SELECT l.platform, l.id AS record_id,
                      o.id AS observation_id, o.source_payload_json
               FROM marketplace_listing_observations AS o
               JOIN marketplace_listings AS l ON l.id = o.listing_id
               WHERE o.id = ?""",
            (source_observation_id,),
        )
        values = _row_dict(cursor, cursor.fetchone())
        if values is None:
            raise SourceObservationNotFound(
                f"No marketplace_listing_observations row with id {source_observation_id}"
            )
        selection = select_ouedkniss_text(values["source_payload_json"])
        return LoadedObservation(
            platform=str(values["platform"]), entity_type=source_entity_type,
            record_id=int(values["record_id"]), observation_id=int(values["observation_id"]),
            field_path=selection.field_path, raw_text=selection.raw_text,
            explicit_attributes=selection.explicit_attributes,
            source_span_start=selection.source_span_start,
            source_span_end=selection.source_span_end,
        )
    raise ValueError(f"Unsupported source entity type: {source_entity_type}")


def persist_observation_result(
    conn: sqlite3.Connection,
    observation: LoadedObservation,
    *,
    versions: EngineVersions = DEFAULT_VERSIONS,
) -> ProcessResult:
    """Persist one observation pipeline result inside the caller's transaction."""
    if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Normalization requires PRAGMA foreign_keys=ON")

    normalized = normalize_text(observation.raw_text, versions.text_profile)
    classification, mentions = extract_product_mentions(
        observation.raw_text,
        profile=versions.text_profile,
        explicit_attributes=observation.explicit_attributes,
    )
    source_ref_id = _get_or_create_source_ref(conn, observation)
    input_hash = _sha256_text(observation.field_path + "\0" + observation.raw_text)

    conn.execute(
        """INSERT INTO normalization_runs
           (source_ref_id, normalization_version, selector_version,
            input_field_path, input_span_start, input_span_end, selected_input_text,
            input_hash, normalized_text, language_code)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_ref_id, normalization_version, selector_version, input_hash)
           DO NOTHING""",
        (source_ref_id, versions.normalization_version, versions.selector_version,
         observation.field_path, observation.source_span_start, observation.source_span_end,
         observation.raw_text, input_hash, normalized.normalized_text,
         normalized.language_code),
    )
    row = _fetch_one_dict(
        conn,
        """SELECT id, selector_version, input_field_path, input_span_start, input_span_end,
                  selected_input_text, normalized_text, language_code
           FROM normalization_runs
           WHERE source_ref_id=? AND normalization_version=? AND selector_version=?
             AND input_hash=?""",
        (source_ref_id, versions.normalization_version, versions.selector_version, input_hash),
    )
    assert row is not None
    if (
        row["selector_version"], row["input_field_path"], row["input_span_start"],
        row["input_span_end"], row["selected_input_text"], row["normalized_text"],
        row["language_code"],
    ) != (
        versions.selector_version, observation.field_path, observation.source_span_start,
        observation.source_span_end, observation.raw_text, normalized.normalized_text,
        normalized.language_code,
    ):
        raise VersionDriftError(
            "stored normalization result differs for the same source/version/input hash"
        )
    normalization_run_id = int(row["id"])

    extraction_hash = _sha256_json({
        "normalized_text": normalized.normalized_text,
        "classification": classification.value.value,
        "classification_rule": classification.rule_id,
        "explicit_attributes": dict(observation.explicit_attributes),
    })
    conn.execute(
        """INSERT INTO normalization_extraction_runs
           (normalization_run_id, extraction_version, input_hash, status)
           VALUES (?, ?, ?, 'complete')
           ON CONFLICT(normalization_run_id, extraction_version, input_hash) DO NOTHING""",
        (normalization_run_id, versions.extraction_version, extraction_hash),
    )
    extraction = _fetch_one_dict(
        conn,
        """SELECT id FROM normalization_extraction_runs
           WHERE normalization_run_id=? AND extraction_version=? AND input_hash=?""",
        (normalization_run_id, versions.extraction_version, extraction_hash),
    )
    assert extraction is not None
    extraction_run_id = int(extraction["id"])

    classification_result_id = _persist_classification(
        conn, source_ref_id, normalization_run_id, extraction_run_id,
        input_hash, classification, versions,
    )

    mention_ids = _persist_mentions(conn, extraction_run_id, mentions)
    candidate_run_ids: list[int] = []
    decision_ids: list[int] = []
    candidate_count = 0
    evidence_count = 0

    # Non-product records are gated before candidate lookup. Their extraction run
    # is still stored with zero mentions, so the same observation/version is not
    # repeatedly processed. Classification is reproducible from that version.
    if classification.value is RecordClass.PRODUCT:
        source_type = "facebook" if observation.entity_type == "facebook_ad" else "marketplace"
        for mention, mention_id in zip(mentions, mention_ids):
            proposals = find_exact_alias_candidates(conn, mention, source_type=source_type)
            evaluation = evaluate_candidates(
                mention, proposals, profile=versions.text_profile,
            )
            candidate_run_id, candidates_by_product, added_evidence = _persist_candidates(
                conn, mention_id, mention, proposals, evaluation, versions,
            )
            candidate_run_ids.append(candidate_run_id)
            candidate_count += len(candidates_by_product)
            evidence_count += added_evidence
            event_id = _persist_decision(
                conn, mention_id, candidates_by_product, evaluation, versions,
            )
            if event_id is not None:
                decision_ids.append(event_id)

    return ProcessResult(
        source_platform=observation.platform,
        source_entity_type=observation.entity_type,
        source_record_id=observation.record_id,
        source_observation_id=observation.observation_id,
        source_ref_id=source_ref_id,
        normalization_run_id=normalization_run_id,
        extraction_run_id=extraction_run_id,
        classification_result_id=classification_result_id,
        classification=classification,
        mention_ids=tuple(mention_ids),
        candidate_run_ids=tuple(candidate_run_ids),
        candidate_count=candidate_count,
        evidence_count=evidence_count,
        decision_event_ids=tuple(decision_ids),
        normalization_version=versions.normalization_version,
        extraction_version=versions.extraction_version,
        matching_version=versions.matching_version,
        classification_version=versions.classification_version,
    )


def _persist_classification(
    conn: sqlite3.Connection,
    source_ref_id: int,
    normalization_run_id: int,
    extraction_run_id: int,
    input_hash: str,
    classification: Classification,
    versions: EngineVersions,
) -> int:
    evidence_json = (
        json.dumps(
            {"matched_terms": list(classification.matched_terms)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        if classification.matched_terms else None
    )
    expected = (
        classification.value.value,
        classification.method,
        classification.rule_id,
        evidence_json,
    )
    conn.execute(
        """INSERT INTO classification_results (
               source_ref_id, normalization_run_id, extraction_run_id,
               classification_version, input_hash, classification_label,
               classification_method, rule_id, evidence_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_ref_id, classification_version, input_hash) DO NOTHING""",
        (source_ref_id, normalization_run_id, extraction_run_id,
         versions.classification_version, input_hash, *expected),
    )
    stored = _fetch_one_dict(
        conn,
        """SELECT id, classification_label, classification_method, rule_id, evidence_json
           FROM classification_results
           WHERE source_ref_id=? AND classification_version=? AND input_hash=?""",
        (source_ref_id, versions.classification_version, input_hash),
    )
    assert stored is not None
    actual = (
        stored["classification_label"], stored["classification_method"],
        stored["rule_id"], stored["evidence_json"],
    )
    if actual != expected:
        raise VersionDriftError(
            "stored classification differs for the same source/version/input hash"
        )
    return int(stored["id"])


def _get_or_create_source_ref(conn: sqlite3.Connection, source: LoadedObservation) -> int:
    identity = (
        source.platform, source.entity_type, source.record_id, source.observation_id,
    )
    existing = _fetch_one_dict(
        conn,
        """SELECT id FROM normalization_source_refs
           WHERE source_platform=? AND source_entity_type=?
             AND source_record_id=? AND source_observation_id=?""",
        identity,
    )
    if existing:
        return int(existing["id"])
    marketplace_id = source.observation_id if source.entity_type == "marketplace_listing" else None
    facebook_id = source.observation_id if source.entity_type == "facebook_ad" else None
    conn.execute(
        """INSERT INTO normalization_source_refs
           (source_platform, source_entity_type, source_record_id, source_observation_id,
            marketplace_observation_id, facebook_observation_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (*identity, marketplace_id, facebook_id),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _persist_mentions(
    conn: sqlite3.Connection,
    extraction_run_id: int,
    mentions: tuple[ProductMention, ...],
) -> list[int]:
    mention_ids: list[int] = []
    for mention in mentions:
        values = _mention_values(extraction_run_id, mention)
        conn.execute(
            """INSERT INTO product_mentions (
                   extraction_run_id, mention_index, source_span_start, source_span_end,
                   source_text_span, normalized_text, language_code, mention_kind,
                   brand, model, category, variant, quantity_value, quantity_unit,
                   capacity_value, capacity_unit, specifications_json, extraction_method,
                   extraction_confidence, extraction_score, extraction_score_type
               ) VALUES (
                   :extraction_run_id, :mention_index, :source_span_start, :source_span_end,
                   :source_text_span, :normalized_text, :language_code, :mention_kind,
                   :brand, :model, :category, :variant, :quantity_value, :quantity_unit,
                   :capacity_value, :capacity_unit, :specifications_json, :extraction_method,
                   :extraction_confidence, NULL, NULL
               )
               ON CONFLICT(extraction_run_id, mention_index) DO NOTHING""",
            values,
        )
        stored = _fetch_one_dict(
            conn,
            """SELECT id, source_span_start, source_span_end, source_text_span,
                      normalized_text, language_code, mention_kind, brand, model,
                      category, variant, quantity_value, quantity_unit, capacity_value,
                      capacity_unit, specifications_json, extraction_method,
                      extraction_confidence
               FROM product_mentions WHERE extraction_run_id=? AND mention_index=?""",
            (extraction_run_id, mention.mention_index),
        )
        assert stored is not None
        expected = values.copy()
        expected.pop("extraction_run_id")
        expected.pop("mention_index")
        expected.pop("extraction_score")
        expected.pop("extraction_score_type")
        for key, value in expected.items():
            if stored[key] != value:
                raise VersionDriftError(
                    f"stored mention output differs for extraction version at field {key}"
                )
        mention_ids.append(int(stored["id"]))
    stored_count = conn.execute(
        "SELECT COUNT(*) FROM product_mentions WHERE extraction_run_id=?",
        (extraction_run_id,),
    ).fetchone()[0]
    if stored_count != len(mentions):
        raise VersionDriftError("stored mention count differs for the same extraction run")
    return mention_ids


def _mention_values(extraction_run_id: int, mention: ProductMention) -> dict[str, Any]:
    return {
        "extraction_run_id": extraction_run_id,
        "mention_index": mention.mention_index,
        "source_span_start": mention.source_span_start,
        "source_span_end": mention.source_span_end,
        "source_text_span": mention.source_text_span,
        "normalized_text": mention.normalized_text,
        "language_code": mention.language_code,
        "mention_kind": mention.mention_kind,
        "brand": mention.brand,
        "model": mention.model,
        "category": mention.category,
        "variant": mention.variant,
        "quantity_value": mention.quantity_value,
        "quantity_unit": mention.quantity_unit,
        "capacity_value": mention.capacity_value,
        "capacity_unit": mention.capacity_unit,
        "specifications_json": mention.specifications_json,
        "extraction_method": mention.extraction_method,
        "extraction_confidence": mention.extraction_confidence,
        "extraction_score": None,
        "extraction_score_type": None,
    }


def _persist_candidates(
    conn: sqlite3.Connection,
    mention_id: int,
    mention: ProductMention,
    proposals: tuple[CandidateProposal, ...],
    evaluation: MatchEvaluation,
    versions: EngineVersions,
) -> tuple[int, dict[int, int], int]:
    catalog_hash = catalog_snapshot_hash(proposals)
    input_hash = candidate_input_hash(
        mention, proposals, matching_version=versions.matching_version,
    )
    conn.execute(
        """INSERT INTO candidate_generation_runs
           (product_mention_id, matching_version, catalog_snapshot_hash, input_hash,
            generation_method, status)
           VALUES (?, ?, ?, ?, 'deterministic', 'complete')
           ON CONFLICT(product_mention_id, matching_version, input_hash) DO NOTHING""",
        (mention_id, versions.matching_version, catalog_hash, input_hash),
    )
    run = _fetch_one_dict(
        conn,
        """SELECT id, catalog_snapshot_hash FROM candidate_generation_runs
           WHERE product_mention_id=? AND matching_version=? AND input_hash=?""",
        (mention_id, versions.matching_version, input_hash),
    )
    assert run is not None
    if run["catalog_snapshot_hash"] != catalog_hash:
        raise VersionDriftError("catalog snapshot differs for the same matcher input hash")
    candidate_run_id = int(run["id"])
    candidate_ids: dict[int, int] = {}
    added_evidence = 0
    for rank, proposal in enumerate(proposals, start=1):
        confidence = "HIGH" if evaluation.state == "MATCHED" else "UNCERTAIN"
        conn.execute(
            """INSERT INTO product_candidates
               (candidate_run_id, product_id, rank, confidence_label,
                score, score_type, scoring_method_version)
               VALUES (?, ?, ?, ?, NULL, NULL, ?)
               ON CONFLICT(candidate_run_id, product_id) DO NOTHING""",
            (candidate_run_id, proposal.product_id, rank, confidence, versions.matching_version),
        )
        candidate = _fetch_one_dict(
            conn,
            "SELECT id, confidence_label FROM product_candidates WHERE candidate_run_id=? AND product_id=?",
            (candidate_run_id, proposal.product_id),
        )
        assert candidate is not None
        if candidate["confidence_label"] != confidence:
            raise VersionDriftError("stored candidate confidence differs under the same matcher version")
        candidate_id = int(candidate["id"])
        candidate_ids[proposal.product_id] = candidate_id
        for alias in proposal.aliases:
            added_evidence += _insert_evidence_if_missing(
                conn, candidate_id, "exact_alias", alias.normalized_alias,
                "product_aliases.normalized_alias", mention.source_span_start,
                mention.source_span_end, versions.matching_version, alias.alias_id,
            )
        if evaluation.candidate_product_id == proposal.product_id:
            for conflict in evaluation.conflicts:
                added_evidence += _insert_evidence_if_missing(
                    conn, candidate_id, "deterministic_rule", conflict,
                    "products.attributes_json", mention.source_span_start,
                    mention.source_span_end, versions.matching_version, None,
                )
    return candidate_run_id, candidate_ids, added_evidence


def _insert_evidence_if_missing(
    conn: sqlite3.Connection,
    candidate_id: int,
    evidence_type: str,
    value: str,
    source: str,
    span_start: int | None,
    span_end: int | None,
    method_version: str,
    alias_id: int | None,
) -> int:
    found = _fetch_one_dict(
        conn,
        """SELECT id FROM candidate_evidence
           WHERE candidate_id=? AND evidence_type=? AND evidence_value=?
             AND evidence_source=? AND ifnull(source_span_start, -1)=ifnull(?, -1)
             AND ifnull(source_span_end, -1)=ifnull(?, -1)
             AND ifnull(method_version, '')=? AND ifnull(product_alias_id, -1)=ifnull(?, -1)
             AND ai_provider_run_id IS NULL""",
        (candidate_id, evidence_type, value, source, span_start, span_end,
         method_version, alias_id),
    )
    if found:
        return 0
    conn.execute(
        """INSERT INTO candidate_evidence
           (candidate_id, evidence_type, evidence_value, evidence_source,
            source_span_start, source_span_end, method_version, product_alias_id,
            evidence_metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
        (candidate_id, evidence_type, value, source, span_start, span_end,
         method_version, alias_id),
    )
    return 1


def _persist_decision(
    conn: sqlite3.Connection,
    mention_id: int,
    candidate_ids: Mapping[int, int],
    evaluation: MatchEvaluation,
    versions: EngineVersions,
) -> int | None:
    selected_candidate_id = (
        candidate_ids.get(evaluation.candidate_product_id)
        if evaluation.candidate_product_id is not None else None
    )
    selected_product_id = evaluation.candidate_product_id
    actor_type = "system" if evaluation.state == "UNMAPPED" else "rule"
    actor_id = "deterministic_normalization_engine"
    method = evaluation.decision_method
    signature = (
        evaluation.state, selected_candidate_id, selected_product_id, actor_type,
        actor_id, method, versions.matching_version, evaluation.reason,
        evaluation.confidence_label,
    )
    latest = _fetch_one_dict(
        conn,
        """SELECT id, revision_no, decision_state, candidate_id, product_id,
                  actor_type, actor_id, decision_method, decision_method_version,
                  reason, confidence_label
           FROM normalization_decision_events
           WHERE product_mention_id=? ORDER BY revision_no DESC LIMIT 1""",
        (mention_id,),
    )
    if latest:
        current_signature = (
            latest["decision_state"], latest["candidate_id"], latest["product_id"],
            latest["actor_type"], latest["actor_id"], latest["decision_method"],
            latest["decision_method_version"], latest["reason"], latest["confidence_label"],
        )
        if current_signature == signature:
            return None
        if latest["actor_type"] == "human":
            return None
        revision = int(latest["revision_no"]) + 1
    else:
        revision = 1

    conn.execute(
        """INSERT INTO normalization_decision_events
           (product_mention_id, revision_no, decision_state, candidate_id, product_id,
            actor_type, actor_id, decision_method, decision_method_version, reason,
            confidence_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mention_id, revision, evaluation.state, selected_candidate_id,
         selected_product_id, actor_type, actor_id, method,
         versions.matching_version, evaluation.reason, evaluation.confidence_label),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _fetch_one_dict(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, params)
    return _row_dict(cursor, cursor.fetchone())


def _row_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(zip((column[0] for column in cursor.description), row))


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(raw)
