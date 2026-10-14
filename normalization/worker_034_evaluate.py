"""Create a read-only, reproducible Worker 034 normalization census.

This script reads the configured Kratzwerk SQLite database in SQLite read-only
mode. It calls the same source selectors, extractor, and alias matcher used by
the persistence engine, but deliberately does not persist normalization rows.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from normalization.match import evaluate_candidates, find_exact_alias_candidates
from normalization.extract import extract_product_mentions
from normalization.persistence import load_source_observation
from normalization.engine import NormalizationEngine
from normalization.text import normalize_text
from normalization.versioning import DEFAULT_VERSIONS


SOURCE_TABLES = {
    "facebook_ad": "facebook_ad_observations",
    "marketplace_listing": "marketplace_listing_observations",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _sha256_json(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _reference_metrics(
    examples: list[dict[str, Any]], reference_labels: dict[str, Any],
) -> dict[str, Any]:
    by_id = {example["example_id"]: example for example in examples}
    labels = reference_labels["examples"]
    if len({label["example_id"] for label in labels}) != len(labels):
        raise ValueError("reference label example IDs must be unique")
    class_labels = ("PRODUCT", "SERVICE", "REAL_ESTATE", "EDUCATION", "UNKNOWN")
    confusion = {truth: Counter() for truth in class_labels}
    source_confusion: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {truth: Counter() for truth in class_labels}
    )
    mention = Counter()
    by_source_mention: dict[str, Counter[str]] = defaultdict(Counter)
    records = []
    for label in labels:
        example_id = label["example_id"]
        if example_id not in by_id:
            raise ValueError(f"reference label example not in current manifest: {example_id}")
        example = by_id[example_id]
        if not example["engine_output"]["eligible_for_text_evaluation"]:
            raise ValueError(f"reference-labeled example lacks local text: {example_id}")
        truth = label["expected_classification"]
        predicted = example["engine_output"]["classification"]["value"]
        actual_mention = bool(example["engine_output"]["mentions"])
        expected_mention = bool(label["expected_mention_present"])
        if truth not in class_labels:
            raise ValueError(f"unsupported reference class label: {truth}")
        confusion[truth][predicted] += 1
        source_confusion[example["source_type"]][truth][predicted] += 1
        if expected_mention and actual_mention:
            mention["tp"] += 1
        elif expected_mention:
            mention["fn"] += 1
        elif actual_mention:
            mention["fp"] += 1
        else:
            mention["tn"] += 1
        source = example["source_type"]
        key = ("tp" if expected_mention else "fp") if actual_mention else (
            "fn" if expected_mention else "tn"
        )
        by_source_mention[source][key] += 1
        records.append({
            "example_id": example_id,
            "source_type": source,
            "expected_classification": truth,
            "predicted_classification": predicted,
            "expected_mention_present": expected_mention,
            "predicted_mention_present": actual_mention,
            "case": label["case"],
        })

    total = len(labels)
    correct = sum(confusion[label][label] for label in class_labels)
    per_class = {}
    for label in class_labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in class_labels if other != label)
        fn = sum(confusion[label][other] for other in class_labels if other != label)
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall else None
        )
        per_class[label] = {
            "support": sum(confusion[label].values()),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    mention_precision = _safe_ratio(mention["tp"], mention["tp"] + mention["fp"])
    mention_recall = _safe_ratio(mention["tp"], mention["tp"] + mention["fn"])
    mention_f1 = (
        2 * mention_precision * mention_recall / (mention_precision + mention_recall)
        if mention_precision is not None and mention_recall is not None
        and mention_precision + mention_recall else None
    )
    return {
        "reference_set_id": reference_labels["reference_set_id"],
        "label_source": reference_labels["label_source"],
        "label_provenance": reference_labels["label_provenance"],
        "sample_size": total,
        "sampling_note": "Purposive inherited case controls; not a random production sample or independent Worker 034 annotation.",
        "classification": {
            "accuracy": _safe_ratio(correct, total),
            "correct": correct,
            "total": total,
            "confusion_matrix_actual_rows_predicted_columns": {
                truth: {predicted: confusion[truth][predicted] for predicted in class_labels}
                for truth in class_labels
            },
            "per_class": per_class,
            "by_source_confusion_matrix_actual_rows_predicted_columns": {
                source: {
                    truth: {predicted: counts[predicted] for predicted in class_labels}
                    for truth, counts in matrix.items()
                }
                for source, matrix in sorted(source_confusion.items())
            },
        },
        "mention_presence_at_observation_level": {
            "tp": mention["tp"],
            "fp": mention["fp"],
            "fn": mention["fn"],
            "tn": mention["tn"],
            "precision": mention_precision,
            "recall": mention_recall,
            "f1": mention_f1,
            "by_source": {
                source: {key: counts[key] for key in ("tp", "fp", "fn", "tn")}
                for source, counts in sorted(by_source_mention.items())
            },
            "scope_note": "Observation-level presence only. Exact span precision/recall/F1 are unavailable because this inherited label set does not contain independently annotated gold spans.",
        },
        "examples": records,
    }


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, cwd=PROJECT_ROOT,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _mention_result(conn: Any, observation: Any, mention: Any) -> dict[str, Any]:
    source_type = "facebook" if observation.entity_type == "facebook_ad" else "marketplace"
    candidates = find_exact_alias_candidates(conn, mention, source_type=source_type)
    evaluation = evaluate_candidates(
        mention, candidates, profile=DEFAULT_VERSIONS.text_profile,
    )
    return {
        "mention": asdict(mention),
        "matching": {
            "state": evaluation.state,
            "candidate_product_id": evaluation.candidate_product_id,
            "candidate_product_ids": [candidate.product_id for candidate in candidates],
            "confidence_label": evaluation.confidence_label,
            "decision_method": evaluation.decision_method,
            "reason": evaluation.reason,
            "conflicts": list(evaluation.conflicts),
        },
    }


def _run_isolated_engine_replay() -> dict[str, Any]:
    """Run persistence on a temporary clone, never against the configured DB."""
    with tempfile.TemporaryDirectory(prefix="worker_034_normalization_") as directory:
        clone_path = Path(directory) / "measurement.sqlite"
        source = config.connect(read_only=True)
        clone = sqlite3.connect(clone_path)
        try:
            source.execute("PRAGMA query_only = ON")
            source.backup(clone)
        finally:
            source.close()

        clone.row_factory = sqlite3.Row
        clone.execute("PRAGMA foreign_keys = ON")
        clone.executescript(
            (PROJECT_ROOT / "normalization" / "schema_v1.sql").read_text(encoding="utf-8")
        )
        engine = NormalizationEngine()
        first_pass = list(engine.process_all(clone))
        first_counts = {
            "normalization_runs": int(clone.execute(
                "SELECT COUNT(*) FROM normalization_runs"
            ).fetchone()[0]),
            "classification_results": int(clone.execute(
                "SELECT COUNT(*) FROM classification_results"
            ).fetchone()[0]),
            "product_mentions": int(clone.execute(
                "SELECT COUNT(*) FROM product_mentions"
            ).fetchone()[0]),
            "normalization_decision_events": int(clone.execute(
                "SELECT COUNT(*) FROM normalization_decision_events"
            ).fetchone()[0]),
        }
        class_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for result in first_pass:
            class_counts[result.source_entity_type][result.classification.value.value] += 1
        decision_counts = {
            str(row[0]): int(row[1]) for row in clone.execute(
                "SELECT decision_state, COUNT(*) FROM normalization_decision_events "
                "GROUP BY decision_state ORDER BY decision_state"
            ).fetchall()
        }
        integrity = str(clone.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = len(clone.execute("PRAGMA foreign_key_check").fetchall())
        clone.close()
        return {
            "temporary_clone_only": True,
            "production_database_writes": False,
            "schema_applied_to_clone_only": True,
            "first_pass_observations": len(first_pass),
            "first_pass_product_mentions": sum(len(result.mention_ids) for result in first_pass),
            "classification_by_source": {
                source: dict(counts) for source, counts in sorted(class_counts.items())
            },
            "decision_state_counts": decision_counts,
            "derived_rows_after_first_pass": first_counts,
            "integrity_check": integrity,
            "foreign_key_violation_count": foreign_key_violations,
        }


def build_outputs() -> tuple[dict[str, Any], dict[str, Any]]:
    db_path = config.DATABASE_PATH
    db_hash_before = _sha256_file(db_path)
    examples: list[dict[str, Any]] = []
    labels_path = PROJECT_ROOT / "normalization" / "evaluation" / "worker_034_reference_labels.json"
    reference_labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels_by_id = {row["example_id"]: row for row in reference_labels["examples"]}
    conn = config.connect(read_only=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")

        source_counts = {
            source: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for source, table in SOURCE_TABLES.items()
        }
        catalog_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("products", "product_aliases", "product_mappings")
        }
        for source_type, table in SOURCE_TABLES.items():
            observation_ids = [
                int(row[0]) for row in conn.execute(
                    f"SELECT id FROM {table} ORDER BY id"
                ).fetchall()
            ]
            for observation_id in observation_ids:
                observation = load_source_observation(conn, source_type, observation_id)
                normalized = normalize_text(
                    observation.raw_text, DEFAULT_VERSIONS.text_profile,
                )
                classification, mentions = extract_product_mentions(
                    observation.raw_text,
                    profile=DEFAULT_VERSIONS.text_profile,
                    explicit_attributes=observation.explicit_attributes,
                )
                if observation.raw_text:
                    evidence_status = "usable_observation_local_text"
                elif observation.field_path.endswith("#no-copy"):
                    evidence_status = "observation_local_field_present_but_no_usable_copy"
                else:
                    evidence_status = "missing_observation_local_text"

                mention_results = [
                    _mention_result(conn, observation, mention) for mention in mentions
                ]
                example = {
                    "example_id": f"{source_type}_observation_{observation_id:06d}",
                    "source_type": source_type,
                    "source_platform": observation.platform,
                    "source_entity_id": observation.record_id,
                    "observation_id": observation.observation_id,
                    "evidence_provenance": {
                        "selected_field_path": observation.field_path,
                        "selection_rule_version": DEFAULT_VERSIONS.selector_version,
                        "evidence_status": evidence_status,
                        "text_sha256": _sha256_json(observation.raw_text),
                    },
                    "observation_local_text": observation.raw_text,
                    "normalization": {
                        "normalized_text": normalized.normalized_text,
                        "language_code": normalized.language_code,
                        "version": normalized.version,
                    },
                    "engine_output": {
                        "eligible_for_text_evaluation": bool(observation.raw_text.strip()),
                        "classification": {
                            "value": classification.value.value,
                            "rule_id": classification.rule_id,
                            "matched_terms": list(classification.matched_terms),
                            "method": classification.method,
                        },
                        "mentions": mention_results,
                        "final_states": (
                            [item["matching"]["state"] for item in mention_results]
                            if mentions else ["NO_MENTION"]
                        ),
                    },
                    "annotation_status": "unlabeled",
                    "inherited_reference_adjudication": labels_by_id.get(
                        f"{source_type}_observation_{observation_id:06d}"
                    ),
                }
                examples.append(example)

        conn.rollback()
    finally:
        conn.close()
    db_hash_after = _sha256_file(db_path)

    label_counts: dict[str, Counter[str]] = defaultdict(Counter)
    eligible_counts: dict[str, Counter[str]] = defaultdict(Counter)
    evidence_counts: dict[str, Counter[str]] = defaultdict(Counter)
    mention_counts: dict[str, Counter[str]] = defaultdict(Counter)
    match_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for example in examples:
        source = example["source_type"]
        output = example["engine_output"]
        label_counts[source][output["classification"]["value"]] += 1
        evidence_counts[source][example["evidence_provenance"]["evidence_status"]] += 1
        if output["eligible_for_text_evaluation"]:
            eligible_counts[source][output["classification"]["value"]] += 1
        mention_counts[source]["observations_with_mentions"] += bool(output["mentions"])
        mention_counts[source]["product_mentions"] += len(output["mentions"])
        for state in output["final_states"]:
            match_counts[source][state] += 1

    example_hash_payload = [
        {
            "example_id": example["example_id"],
            "selected_field_path": example["evidence_provenance"]["selected_field_path"],
            "text_sha256": example["evidence_provenance"]["text_sha256"],
        }
        for example in examples
    ]
    generated_at = datetime.now(timezone.utc)
    reviewed_subset_metrics = _reference_metrics(examples, reference_labels)
    isolated_replay = _run_isolated_engine_replay()
    replay_matches_pure_pipeline = (
        isolated_replay["first_pass_observations"] == len(examples)
        and isolated_replay["first_pass_product_mentions"]
        == sum(len(example["engine_output"]["mentions"]) for example in examples)
        and isolated_replay["classification_by_source"]
        == {source: dict(counts) for source, counts in sorted(label_counts.items())}
    )
    if not replay_matches_pure_pipeline:
        raise RuntimeError("temporary persistence replay differs from pure evaluation outputs")
    isolated_replay["pure_pipeline_outputs_match"] = True
    manifest = {
        "manifest_id": "worker_034_normalization_real_observations_v1",
        "manifest_version": 1,
        "generated_at_utc": generated_at.isoformat(timespec="seconds"),
        "source_database": str(db_path),
        "source_database_sha256_before": db_hash_before,
        "source_database_sha256_after": db_hash_after,
        "source_database_unchanged": db_hash_before == db_hash_after,
        "source_read_only": True,
        "production_writes": False,
        "sampling": {
            "method": "complete census of all rows in the configured observation tables",
            "inclusion": "Every facebook_ad_observations and marketplace_listing_observations row present in the read transaction.",
            "ordering": "source_type in SOURCE_TABLES insertion order, then observation id ascending.",
            "random_seed": None,
            "excluded_observations": 0,
            "insufficient_text_observations_retained_and_labeled": True,
            "mutable_entity_fields_used_as_historical_evidence": False,
        },
        "reproducibility": {
            "command": ".\\.venv\\Scripts\\python.exe normalization\\worker_034_evaluate.py",
            "git_revision": _git_revision(),
            "python_version": platform.python_version(),
            "normalization": DEFAULT_VERSIONS.normalization_version,
            "selector": DEFAULT_VERSIONS.selector_version,
            "classification": DEFAULT_VERSIONS.classification_version,
            "extraction": DEFAULT_VERSIONS.extraction_version,
            "matching": DEFAULT_VERSIONS.matching_version,
            "script_sha256": _sha256_file(Path(__file__).resolve()),
            "example_set_sha256": _sha256_json(example_hash_payload),
        },
        "catalog_counts": catalog_counts,
        "source_counts": source_counts,
        "isolated_persistence_engine_replay": isolated_replay,
        "pure_pipeline_outputs_match_isolated_replay": replay_matches_pure_pipeline,
        "aggregate_outputs": {
            "classification_all_observations_by_source": {
                source: dict(counts) for source, counts in sorted(label_counts.items())
            },
            "classification_text_eligible_by_source": {
                source: dict(counts) for source, counts in sorted(eligible_counts.items())
            },
            "evidence_availability_by_source": {
                source: dict(counts) for source, counts in sorted(evidence_counts.items())
            },
            "mention_counts_by_source": {
                source: dict(counts) for source, counts in sorted(mention_counts.items())
            },
            "final_match_state_counts_by_source": {
                source: dict(counts) for source, counts in sorted(match_counts.items())
            },
        },
        "ground_truth": {
            "worker_034_independent_labels": 0,
            "inherited_adjudicated_examples": reviewed_subset_metrics["sample_size"],
            "inherited_control_metrics": reviewed_subset_metrics,
            "production_census_metrics": "not computed: the complete census is unlabeled",
            "template_path": "normalization/evaluation/worker_034_annotation_template.json",
            "reference_labels_path": "normalization/evaluation/worker_034_reference_labels.json",
        },
        "limitations": [
            "Deterministic outputs on unlabeled observations are not ground truth.",
            "The empty production catalog blocks meaningful exact-alias identity accuracy measurement.",
            "Observations with missing or unusable observation-local text remain in the manifest and are not backfilled from mutable entity fields.",
        ],
        "examples": examples,
    }

    results = {
        "report_id": "worker_034_normalization_evaluation_v1",
        "manifest_id": manifest["manifest_id"],
        "source_database_sha256_before": db_hash_before,
        "source_database_sha256_after": db_hash_after,
        "source_database_unchanged": db_hash_before == db_hash_after,
        "production_writes": False,
        "source_counts": source_counts,
        "catalog_counts": catalog_counts,
        "isolated_persistence_engine_replay": isolated_replay,
        "pure_pipeline_outputs_match_isolated_replay": replay_matches_pure_pipeline,
        "classification_all_observations_by_source": manifest["aggregate_outputs"]["classification_all_observations_by_source"],
        "classification_text_eligible_by_source": manifest["aggregate_outputs"]["classification_text_eligible_by_source"],
        "evidence_availability_by_source": manifest["aggregate_outputs"]["evidence_availability_by_source"],
        "mention_counts_by_source": manifest["aggregate_outputs"]["mention_counts_by_source"],
        "final_match_state_counts_by_source": manifest["aggregate_outputs"]["final_match_state_counts_by_source"],
        "ground_truth_metrics": {
            "worker_034_independent_labels": 0,
            "production_census_classification_precision_recall_f1": None,
            "inherited_control_subset": reviewed_subset_metrics,
            "span_level_mention_extraction_precision_recall_f1": None,
            "exact_alias_accuracy": None,
            "reason": "The inherited adjudication controls support limited classification and mention-presence checks only. Full-census accuracy, exact span scoring, and production identity accuracy require new independent annotations/catalog evidence.",
        },
        "synthetic_benchmark": {
            "worker_018_manifest_is_not_scored_as_production_accuracy": True,
            "worker_021_direct_current_pipeline_reference": "reports/worker_021_measurement_results.json",
            "worker_021_adjudication_reference": "reports/worker_021_adjudication.md",
        },
        "offline_ai": {
            "run": False,
            "decision": "NO AI YET",
            "proposed_experiment_design": {
                "task": "Five-way classification on observation-local text only.",
                "comparison": "Freeze the deterministic_v2 outputs and compare one offline model pass on the same independently labeled examples.",
                "output_schema": {
                    "classification": ["PRODUCT", "SERVICE", "REAL_ESTATE", "EDUCATION", "UNKNOWN"],
                    "evidence_quotes": "short exact substrings from the supplied text",
                    "abstain": "boolean",
                },
                "evaluation": "Per-class precision/recall/F1, macro F1, abstentions, failures, and a reviewed error table; keep the exact source-observation split fixed.",
                "split_rule": "Use a source-stratified held-out split only after enough independent labels exist to support it; otherwise report a single-pass exploratory result with no statistical claim.",
                "isolation": "Local/offline execution on a frozen subset; no production DB connection, no writes, no price task, and no identity approval.",
                "required_run_record": "Provider/model and version, prompt/config version, input/output schema, settings, failures, cost if available, and reproducibility limits.",
            },
            "blockers": [
                "No offline model/provider integration is configured in this repository/runtime.",
                "No adequately sized held-out, independently labeled production subset exists.",
                "Identity evaluation is blocked by zero production products, aliases, and mappings.",
            ],
        },
    }
    return manifest, results


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def _build_annotation_template(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": "worker_034_human_annotation_v1",
        "manifest_id": manifest["manifest_id"],
        "instructions": {
            "review_unit": "one observation and its exact observation-local text from the manifest",
            "classification_labels": [
                "PRODUCT", "SERVICE", "REAL_ESTATE", "EDUCATION", "UNKNOWN",
            ],
            "evidence_rule": "Use only the observation_local_text and evidence_provenance in the manifest. Do not use current entity fields to fill historical gaps.",
            "missing_text_rule": "Mark source_evidence_sufficient=false and leave classification.label null when no usable local text is present; do not guess from an empty input.",
            "mention_rule": "List each product mention once. Span offsets are zero-based Python Unicode code-point indexes with an exclusive end, measured against the exact observation_local_text. Keep the span to the shortest evidenced identifying phrase; do not include unrelated marketing copy or UI text.",
            "sufficiency_rule": "Set sufficiently_evidenced=false when the text does not support that mention; use UNKNOWN classification if the record type itself is not defensible.",
            "identity_rule": "Only enter known_alias or canonical_product_identity when independently verifiable. Use REVIEW for unresolved ambiguity/conflict and UNMAPPED when no defensible catalog identity is available. Never invent a canonical ID.",
            "unreviewed_values": "Null means not reviewed. After review, mentions=[] means reviewed and no product mention was found.",
        },
        "examples": [
            {
                "example_id": example["example_id"],
                "review_status": "NOT_REVIEWED",
                "annotator_id": None,
                "reviewed_at": None,
                "source_evidence_sufficient": None,
                "classification": {"label": None, "rationale": None},
                "mentions": None,
                "notes": None,
            }
            for example in manifest["examples"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("normalization/evaluation/worker_034_manifest.json"),
    )
    parser.add_argument(
        "--results", type=Path,
        default=Path("reports/worker_034_results.json"),
    )
    parser.add_argument(
        "--template", type=Path,
        default=Path("normalization/evaluation/worker_034_annotation_template.json"),
    )
    args = parser.parse_args(argv)
    manifest, results = build_outputs()
    _write_json(args.manifest, manifest)
    _write_json(args.results, results)
    _write_json(args.template, _build_annotation_template(manifest))
    summary = {
        "manifest": str(args.manifest),
        "results": str(args.results),
        "template": str(args.template),
        "source_counts": results["source_counts"],
        "catalog_counts": results["catalog_counts"],
        "source_database_unchanged": results["source_database_unchanged"],
    }
    sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0 if results["source_database_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
