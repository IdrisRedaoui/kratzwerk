"""Prepare the Worker 035 blinded annotation sample from the Worker 034 census.

Run normalization/worker_034_evaluate.py first. This script reads its census
artifacts, hashes the configured database read-only, and writes only the
Worker 035 evaluation artifacts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config

SEED = "worker035-2026-10-17-v1"
SOURCE_ORDER = ("facebook_ad", "marketplace_listing")
SOURCE_NAMES = {
    "facebook_ad": "Facebook",
    "marketplace_listing": "Ouedkniss",
}
TARGET_COUNTS = {
    "facebook_ad": {
        ("mentions", "PRODUCT"): 3,
        ("no_mentions", "SERVICE"): 3,
        ("no_mentions", "EDUCATION"): 3,
        ("no_mentions", "UNKNOWN"): 38,
    },
    "marketplace_listing": {
        ("mentions", "PRODUCT"): 5,
        ("no_mentions", "UNKNOWN"): 1,
        ("no_mentions", "SERVICE"): 1,
        ("no_mentions", "REAL_ESTATE"): 5,
    },
}

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>{}\[\]\"]+")
HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9._-]{3,}")
SECRET_RE = re.compile(
    r"(?i)\b(?:bearer\s+|(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"token|secret|password)\s*[:=]\s*)[A-Za-z0-9._~+/=-]{8,}"
)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+|00)?213(?:[\s()./-]*\d){9}(?!\d)|"
    r"(?<!\d)0[2-7](?:[\s()./-]*\d){7,8}(?!\d)|"
    r"(?<!\w)\+[1-9](?:[\s()./-]*\d){7,14}(?!\w)"
)
SENSITIVE_NARRATIVE_RE = re.compile(
    r"(?i)(?:virginit|membre couvert|\bcorps\b|\bsang\b|\bblood\b|"
    r"\benceinte\b|\bpregnan\w*|maltrait\w*|\babus\w*|"
    r"\bconqu[eê]te\w*|\bs[eé]dui\w*|\btrahie\b|\bmariage\b|"
    r"\bfianc[eé]\w*|\bcowboy\b|\bbad boy\b|\bfausse relation\b)"
)
SENSITIVE_PATTERNS = {
    "credential_or_token": SECRET_RE,
    "email": EMAIL_RE,
    "url": URL_RE,
    "phone": PHONE_RE,
    "social_handle": HANDLE_RE,
}
POST_SCAN_PATTERNS = {
    "credential_or_token": SECRET_RE,
    "email": EMAIL_RE,
    "url": URL_RE,
    "phone": PHONE_RE,
    "social_handle": HANDLE_RE,
}


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


def normalize_decimal_digits(text: str) -> str:
    return "".join(
        str(unicodedata.digit(char)) if char.isdecimal() else char
        for char in text
    )


def redact_text(text: str) -> tuple[str, dict[str, Any]]:
    spans: list[tuple[int, int, str]] = []
    for category, pattern in SENSITIVE_PATTERNS.items():
        scanned = normalize_decimal_digits(text) if category == "phone" else text
        spans.extend((m.start(), m.end(), category) for m in pattern.finditer(scanned))
    spans.sort()
    merged: list[list[Any]] = []
    for start, end, category in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2].add(category)
        else:
            merged.append([start, end, {category}])
    counts: Counter[str] = Counter()
    pieces: list[str] = []
    cursor = 0
    for start, end, categories in merged:
        pieces.append(text[cursor:start])
        ordered = sorted(categories)
        pieces.append("[REDACTED " + "+".join(ordered) + "]")
        cursor = end
        for category in categories:
            counts[category] += 1
    pieces.append(text[cursor:])
    return "".join(pieces), {
        "redacted": bool(merged),
        "redacted_span_count": len(merged),
        "redaction_counts_by_type": dict(sorted(counts.items())),
        "redaction_types": sorted(counts),
    }


def sample_rank(example_id: str) -> str:
    return hashlib.sha256(f"{SEED}\0{example_id}".encode("utf-8")).hexdigest()


def stratum(example: dict[str, Any]) -> tuple[str, str]:
    output = example["engine_output"]
    if output["mentions"]:
        return "mentions", output["classification"]["value"]
    return "no_mentions", output["classification"]["value"]


def select_sample(manifest: dict[str, Any], reference: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible = [
        row for row in manifest["examples"]
        if row["engine_output"]["eligible_for_text_evaluation"]
    ]
    inherited_ids = {row["example_id"] for row in reference["examples"]}
    if not inherited_ids:
        raise ValueError("Worker 034 inherited reference set is empty")
    manifest_inherited_ids = {
        row["example_id"] for row in manifest["examples"]
        if row.get("inherited_reference_adjudication")
    }
    if manifest_inherited_ids != inherited_ids:
        raise ValueError("Worker 034 manifest and inherited reference IDs differ")

    pools: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = defaultdict(list)
    screening_exclusions: Counter[str] = Counter()
    for row in eligible:
        if row["example_id"] in inherited_ids:
            continue
        text = row["observation_local_text"]
        if SENSITIVE_NARRATIVE_RE.search(text):
            screening_exclusions[row["source_type"] + ":adult_or_abuse_narrative"] += 1
            continue
        words = re.findall(r"\b[^\W_]+\b", text, flags=re.UNICODE)
        if (
            not row["engine_output"]["mentions"]
            and row["engine_output"]["classification"]["value"] == "UNKNOWN"
            and len(words) == 1
        ):
            screening_exclusions[row["source_type"] + ":single_word_fragment"] += 1
            continue
        pools[(row["source_type"], stratum(row))].append(row)

    selected: list[dict[str, Any]] = []
    stratum_summary: dict[str, dict[str, Any]] = {}
    duplicate_candidates_by_source: Counter[str] = Counter()
    for source in SOURCE_ORDER:
        stratum_summary[source] = {}
        for key, target in TARGET_COUNTS[source].items():
            pool = pools[(source, key)]
            ranked = sorted(pool, key=lambda row: sample_rank(row["example_id"]))
            chosen: list[dict[str, Any]] = []
            seen_text_hashes: set[str] = set()
            for row in ranked:
                text_hash = row["evidence_provenance"]["text_sha256"]
                if text_hash in seen_text_hashes:
                    continue
                seen_text_hashes.add(text_hash)
                chosen.append(row)
                if len(chosen) == target:
                    break
            unique_text_count = len({
                row["evidence_provenance"]["text_sha256"] for row in pool
            })
            duplicate_candidates_by_source[source] += len(pool) - unique_text_count
            if len(chosen) < target:
                raise ValueError(
                    f"insufficient unique-text candidates for {source} {key}: "
                    f"{unique_text_count} < {target}"
                )
            selected.extend(chosen)
            stratum_summary[source]["/".join(key)] = {
                "candidates_after_control_and_content_screening": len(pool),
                "unique_text_candidates": unique_text_count,
                "duplicate_text_candidates": len(pool) - unique_text_count,
                "sampled": target,
            }

    source_index = {source: index for index, source in enumerate(SOURCE_ORDER)}
    selected.sort(key=lambda row: (
        source_index[row["source_type"]], row["observation_id"],
    ))
    return selected, {
        "eligible_census": len(eligible),
        "inherited_controls_excluded_from_sample": len(inherited_ids),
        "sample_candidate_pool_after_control_exclusion": len(eligible) - len(inherited_ids),
        "screening_exclusions_by_source_and_reason": dict(sorted(screening_exclusions.items())),
        "screening_exclusion_total": sum(screening_exclusions.values()),
        "duplicate_text_candidates_not_selected_by_source": {
            source: duplicate_candidates_by_source[source] for source in SOURCE_ORDER
        },
        "strata": stratum_summary,
    }


def annotation_template(example: dict[str, Any], sample_id: str) -> dict[str, Any]:
    shown_text, redaction = redact_text(example["observation_local_text"])
    source_type = example["source_type"]
    return {
        "sample_id": sample_id,
        "source_type": source_type,
        "source_name": SOURCE_NAMES[source_type],
        "observation_ref": {
            "example_id": example["example_id"],
            "observation_id": example["observation_id"],
            "selected_field_path": example["evidence_provenance"]["selected_field_path"],
            "selection_rule_version": example["evidence_provenance"]["selection_rule_version"],
        },
        "evidence_text": shown_text,
        "evidence_sha256": example["evidence_provenance"]["text_sha256"],
        "display_text_sha256": sha256_bytes(shown_text.encode("utf-8")),
        "privacy_redaction": redaction,
        "human_annotation": {
            "classification": None,
            "exact_product_name_spans": None,
            "normalized_product_name": None,
            "evidence_sufficiency": None,
            "identity_status": None,
            "reviewer_rationale": None,
        },
    }


def baseline_record(example: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    output = example["engine_output"]
    mentions = []
    mention_redactions: Counter[str] = Counter()
    for result in output["mentions"]:
        mention = dict(result["mention"])
        span, redaction = redact_text(str(mention.get("source_text_span") or ""))
        mention["source_text_span"] = span
        if redaction["redacted"]:
            mention_redactions.update(redaction["redaction_counts_by_type"])
        mentions.append({"mention": mention, "matching": result["matching"]})
    return {
        "sample_id": sample["sample_id"],
        "source_type": sample["source_type"],
        "observation_ref": sample["observation_ref"],
        "engine_input_sha256": sample["evidence_sha256"],
        "reviewer_display_text_sha256": sample["display_text_sha256"],
        "baseline_input_note": "The deterministic output was produced by Worker 034 from the exact source text before privacy redaction. Redactions affect only contact or credential-like spans; compare against the separately supplied display-text hash.",
        "classification": output["classification"],
        "mentions": mentions,
        "final_states": output["final_states"],
        "sensitive_spans_redacted_from_baseline": dict(sorted(mention_redactions.items())),
    }


def reviewer_guide() -> str:
    return """# Worker 035 — Reviewer Guide

## Review scope

Review one observation at a time using only its displayed `evidence_text`. The text comes from that exact historical observation and may contain explicit `[REDACTED …]` placeholders for personal contact or credential-like content. Do not replace missing or redacted content with current listing or ad fields. Do not use the separate baseline file while assigning labels; it contains deterministic system output, not ground truth.

The annotation JSON intentionally leaves every label field `null`. Keep `null` for work not completed. After review, use an empty list for `exact_product_name_spans` when no product name is present.

## Classification

- `PRODUCT`: a physical or digital good is being offered, requested, or clearly described as the subject of the observation.
- `SERVICE`: the offer is work or an action, such as repair, rental, installation, or consulting. A product mentioned only as the object of a service is not automatically a product offer.
- `REAL_ESTATE`: the subject is land, a home, or other property for sale or rent.
- `EDUCATION`: the subject is a course, school, training, or educational program.
- `UNKNOWN`: usable text exists, but it does not support any of the four classes well enough to choose one. Do not infer an offer from a slogan, vague promotion, or an isolated generic word.

Choose the best supported class when one is clear. Use `UNKNOWN` when no class is defensible. Do not use `UNKNOWN` just because a brand or model is unfamiliar.

## Product-name spans and normalization

Copy each product-identifying span verbatim from `evidence_text`, including the shortest wording that identifies the item. Preserve the evidence's spelling, script, and punctuation in the span. Add separate spans for separate products. Do not include unrelated sales language, contact details, or unsupported attributes. If a product is clear but no exact name can be copied, leave the span list empty and explain why.

Enter `normalized_product_name` only when a concise canonicalized form is defensible from the evidence. Normalize spacing or obvious casing only; do not translate, expand an abbreviation, guess a brand, or supply a model number. Leave it `null` when no single defensible normalized name exists.

Examples (illustrative only):

| Evidence | Classification | Product-name span / rationale |
| --- | --- | --- |
| `Aspirateur ACME VX-2, neuf` | `PRODUCT` | Span `Aspirateur ACME VX-2`; normalize to `Aspirateur ACME VX-2` if a canonicalized copy is useful. |
| `Réparation de téléphones à domicile` | `SERVICE` | Phones are the service object; there is no product offer to span. |
| `Appartement T2 à louer` | `REAL_ESTATE` | Property rental. |
| `Cours du soir en comptabilité` | `EDUCATION` | Educational service. |
| `Le meilleur choix pour votre maison` | `UNKNOWN` | Promotional text without a supported item or service. |

## Evidence sufficiency

- `SUFFICIENT`: the displayed text supports a stable classification and any product span you record.
- `INSUFFICIENT`: the text is too short, fragmentary, or incomplete to determine the class or exact product wording.
- `AMBIGUOUS`: two or more materially different readings remain plausible from the text (for example, a product sale versus a repair request). Record the best class only if one interpretation is clearly stronger; otherwise use `UNKNOWN` and explain the competing readings.

`UNKNOWN` is a classification; `AMBIGUOUS` and `INSUFFICIENT` describe evidence quality. They are not interchangeable. A clear non-offer can be `UNKNOWN` with `SUFFICIENT`; a fragment can be `UNKNOWN` with `INSUFFICIENT`; unresolved competing readings are `AMBIGUOUS`.

## Identity status

- `KNOWN`: the evidence identifies a specific product that you can confidently connect to a known canonical product.
- `NEW_OR_UNLISTED`: the product is clearly identified, but no matching canonical product is present in the review reference set. No catalog is bundled here and the production catalog was empty at preparation time; do not create a catalog entry as part of annotation.
- `UNCERTAIN`: the product class is clear, but its identity cannot be resolved from the evidence, or multiple plausible identities remain.
- `NOT_APPLICABLE`: the classification is not `PRODUCT`, or no product identity is being annotated.

Do not invent a canonical identity. If you cannot distinguish `KNOWN` from `NEW_OR_UNLISTED`, use `UNCERTAIN` and state the reason briefly.

## Rationale and consistency

Write one short reason grounded in the displayed text. Call out missing context, competing readings, or a redaction if it affects the decision. Do not include personal contact details in the rationale. Use the same decision rules across both source types.
"""


def main() -> int:
    evaluation = ROOT / "normalization" / "evaluation"
    manifest_path = evaluation / "worker_034_manifest.json"
    results_path = ROOT / "reports" / "worker_034_results.json"
    reference_path = evaluation / "worker_034_reference_labels.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prior_results = json.loads(results_path.read_text(encoding="utf-8"))
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    database_path = config.DATABASE_PATH
    db_before = sha256_file(database_path)
    if not manifest.get("source_database_unchanged"):
        raise ValueError("Worker 034 reproduction did not report an unchanged database")
    if db_before != manifest["source_database_sha256_after"]:
        raise ValueError("configured database hash does not match Worker 034 census")
    if prior_results.get("source_database_sha256_after") != db_before:
        raise ValueError("Worker 034 results and manifest database hashes differ")

    selected, selection_counts = select_sample(manifest, reference)
    annotation_examples = [
        annotation_template(row, f"W035-{index:03d}")
        for index, row in enumerate(selected, start=1)
    ]
    baseline_examples = [
        baseline_record(row, sample)
        for row, sample in zip(selected, annotation_examples, strict=True)
    ]
    sample_set_sha256 = sha256_json([
        {
            "sample_id": sample["sample_id"],
            "example_id": sample["observation_ref"]["example_id"],
            "evidence_sha256": sample["evidence_sha256"],
        }
        for sample in annotation_examples
    ])
    db_after = sha256_file(database_path)
    if db_before != db_after:
        raise ValueError("production database hash changed while preparing package")

    source_counts = Counter(row["source_type"] for row in selected)
    mention_counts = Counter(
        row["source_type"] for row in selected if row["engine_output"]["mentions"]
    )
    baseline_class_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in selected:
        baseline_class_counts[row["source_type"]][
            row["engine_output"]["classification"]["value"]
        ] += 1
    redaction_counts: Counter[str] = Counter()
    redacted_records = 0
    for sample in annotation_examples:
        summary = sample["privacy_redaction"]
        redaction_counts.update(summary["redaction_counts_by_type"])
        redacted_records += bool(summary["redacted"])
    baseline_redactions = Counter()
    for row in baseline_examples:
        baseline_redactions.update(row["sensitive_spans_redacted_from_baseline"])

    annotation_doc = {
        "package_id": "worker_035_human_annotation_sample_v1",
        "source_manifest_id": manifest["manifest_id"],
        "sample_size": len(annotation_examples),
        "instructions": {
            "review_unit": "one exact historical observation and its displayed local evidence text",
            "classification_values": ["PRODUCT", "SERVICE", "REAL_ESTATE", "EDUCATION", "UNKNOWN"],
            "evidence_sufficiency_values": ["SUFFICIENT", "INSUFFICIENT", "AMBIGUOUS"],
            "identity_status_values": ["KNOWN", "NEW_OR_UNLISTED", "UNCERTAIN", "NOT_APPLICABLE"],
            "unreviewed_value": "null means not reviewed; use an empty span list after review when no product name is present",
            "privacy_note": "Contact or credential-like spans are replaced with explicit placeholders; raw evidence hashes retain provenance without copying the redacted values.",
        },
        "sampling": {
            "method": "Deterministic, source-stratified and engine-mention-stratified sample, with class strata for no-mention examples; adult, abuse-related, or romance-story copy and one-word UNKNOWN fragments were screened out, exact duplicate evidence is represented once per source, and SHA-256 ranks unique texts within each stratum using the recorded seed. The target was 60; the available unique Ouedkniss evidence capped the final sample at 59.",
            "seed": SEED,
            "sample_set_sha256": sample_set_sha256,
            "source_counts": {source: source_counts[source] for source in SOURCE_ORDER},
            "examples": annotation_examples,
        },
    }
    baseline_doc = {
        "baseline_id": "worker_035_deterministic_baseline_v1",
        "source_manifest_id": manifest["manifest_id"],
        "engine_versions": manifest["reproducibility"],
        "is_read_only_baseline": True,
        "ground_truth": False,
        "sample_size": len(baseline_examples),
        "sampling_seed": SEED,
        "sample_set_sha256": sample_set_sha256,
        "summary": {
            "source_counts": {source: source_counts[source] for source in SOURCE_ORDER},
            "observations_with_current_deterministic_mentions_by_source": {
                source: mention_counts[source] for source in SOURCE_ORDER
            },
            "baseline_classification_counts_by_source": {
                source: dict(sorted(baseline_class_counts[source].items()))
                for source in SOURCE_ORDER
            },
            "interpretation": "These are frozen deterministic system outputs for comparison; they are not human labels or accuracy estimates.",
        },
        "examples": baseline_examples,
    }

    # Check the serialized package for common credential/contact patterns.
    report_path = ROOT / "reports" / "worker_035_results.json"
    annotation_path = evaluation / "worker_035_annotation_sample.json"
    baseline_path = evaluation / "worker_035_baseline.json"
    guide_path = evaluation / "worker_035_reviewer_guide.md"
    annotation_path.write_text(json.dumps(annotation_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    baseline_path.write_text(json.dumps(baseline_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guide_path.write_text(reviewer_guide(), encoding="utf-8")

    privacy_texts = [sample["evidence_text"] for sample in annotation_examples]
    privacy_texts.extend(
        str(mention["mention"].get("source_text_span") or "")
        for row in baseline_examples for mention in row["mentions"]
    )
    privacy_texts.append(guide_path.read_text(encoding="utf-8"))
    post_scan = {}
    for category, pattern in POST_SCAN_PATTERNS.items():
        post_scan[category] = sum(
            len(pattern.findall(normalize_decimal_digits(value) if category == "phone" else value))
            for value in privacy_texts
        )
    if any(post_scan.values()):
        raise ValueError(f"privacy scan found unredacted contact/credential patterns: {post_scan}")

    now = datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds")
    sample_class_counts = {
        source: dict(sorted(baseline_class_counts[source].items()))
        for source in SOURCE_ORDER
    }
    result_doc = {
        "report_id": "worker_035_annotation_package_v1",
        "prepared_at": now,
        "database_path_reference": "data/Database.sqlite",
        "database_sha256_before": db_before,
        "database_sha256_after": db_after,
        "database_unchanged": db_before == db_after,
        "production_database_writes": False,
        "census": {
            "all_observations": manifest["source_counts"],
            "all_observations_total": sum(manifest["source_counts"].values()),
            "eligible_observations": {
                source: sum(
                    1 for row in manifest["examples"]
                    if row["source_type"] == source
                    and row["engine_output"]["eligible_for_text_evaluation"]
                ) for source in SOURCE_ORDER
            },
            "eligible_total": selection_counts["eligible_census"],
            "ineligible_observations": sum(manifest["source_counts"].values()) - selection_counts["eligible_census"],
            "source_selector_versions": {
                source: manifest["reproducibility"]["selector"] for source in SOURCE_ORDER
            },
            "mutable_entity_fields_used_as_evidence": False,
        },
        "sample": {
            "size": len(annotation_examples),
            "sample_set_sha256": sample_set_sha256,
            "source_distribution": {
                SOURCE_NAMES[source]: source_counts[source] for source in SOURCE_ORDER
            },
            "current_deterministic_mention_presence": {
                SOURCE_NAMES[source]: {
                    "with_mention": mention_counts[source],
                    "without_mention": source_counts[source] - mention_counts[source],
                } for source in SOURCE_ORDER
            },
            "baseline_classification_counts_by_source": {
                SOURCE_NAMES[source]: sample_class_counts[source] for source in SOURCE_ORDER
            },
            "method": "The target source quota follows largest-remainder apportionment of 60 across the 129 Facebook and 34 Ouedkniss text-eligible observations (47/13). After excluding 18 inherited controls, screened content, and duplicate local text within each source, only 12 distinct Ouedkniss texts remained; the final sample is therefore 47 Facebook plus 12 Ouedkniss observations (59 total). No-mention strata include every current non-product class with usable examples; SHA-256 rank within each stratum uses the recorded seed. The sample is challenge-enriched and is not a prevalence estimator.",
            "seed": SEED,
            "strata_and_counts": selection_counts["strata"],
            "inherited_controls_excluded": selection_counts["inherited_controls_excluded_from_sample"],
        },
        "exclusions": {
            "ineligible_observations_without_usable_local_text": sum(manifest["source_counts"].values()) - selection_counts["eligible_census"],
            "inherited_prior_adjudication_controls_excluded_from_sample": selection_counts["inherited_controls_excluded_from_sample"],
            "exact_duplicate_text_candidates_not_selected_by_source": selection_counts["duplicate_text_candidates_not_selected_by_source"],
            "exact_duplicate_text_candidates_not_selected_total": sum(selection_counts["duplicate_text_candidates_not_selected_by_source"].values()),
            "content_screening_exclusions_by_source_and_reason": selection_counts["screening_exclusions_by_source_and_reason"],
            "content_screening_exclusion_total": selection_counts["screening_exclusion_total"],
            "other_observations_excluded_after_documented_screening": 0,
            "reason": "The sample contains only usable observation-local evidence; the 18 inherited adjudication controls were held out so this new annotation set remains independent of prior labels. Adult, abuse-related, or romance-story ads and one-word UNKNOWN fragments were excluded to minimize unnecessary sensitive or potentially personal content. Exact duplicate local text within a source was represented once to avoid redundant reviews; this reduced the final sample to 59.",
        },
        "privacy": {
            "redacted_evidence_records": redacted_records,
            "redacted_evidence_spans_by_type": dict(sorted(redaction_counts.items())),
            "redacted_baseline_mention_spans_by_type": dict(sorted(baseline_redactions.items())),
            "post_redaction_scan_matches": post_scan,
            "scan_passed": not any(post_scan.values()),
            "reviewed_fields": ["observation-local evidence text", "deterministic mention spans", "generated guide and reports"],
            "redaction_policy": "Email addresses, URLs, phone-number patterns, social handles, and credential/token assignments were redacted from packaged evidence. The underlying database was not altered; the exact source text SHA-256 and selected field path are retained.",
        },
        "reproduction": {
            "commands": [
                ".\\.venv\\Scripts\\python.exe normalization\\worker_034_evaluate.py",
                ".\\.venv\\Scripts\\python.exe normalization\\worker_035_prepare.py",
            ],
            "worker_034_manifest_id": manifest["manifest_id"],
            "worker_034_git_revision": manifest["reproducibility"]["git_revision"],
            "worker_034_script_sha256": manifest["reproducibility"]["script_sha256"],
            "worker_035_script_sha256": sha256_file(Path(__file__).resolve()),
            "python_version": manifest["reproducibility"]["python_version"],
            "worker_034_versions": manifest["reproducibility"],
            "worker_035_reads_database_as": "file hash only; no SQLite connection",
        },
        "outputs": [
            "reports/worker_035_annotation_package.md",
            "reports/worker_035_results.json",
            "normalization/evaluation/worker_035_annotation_sample.json",
            "normalization/evaluation/worker_035_reviewer_guide.md",
            "normalization/evaluation/worker_035_baseline.json",
        ],
    }
    report_path.write_text(json.dumps(result_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source_rows = "\n".join(
        f"| {SOURCE_NAMES[source]} | {source_counts[source]} | {mention_counts[source]} | {source_counts[source] - mention_counts[source]} |"
        for source in SOURCE_ORDER
    )
    report = f"""# Worker 035 — Human-Labeled Normalisation Evaluation Set

Prepared: {now} (Asia/Riyadh)

## Package

This package prepares a blinded, human annotation set and a separate frozen deterministic baseline. No AI call was made and no labels were assigned automatically as ground truth. The production catalog remains empty; baseline identity states describe the current matcher output only.

## Census and sample

The Worker 034 read-only reproduction found **{sum(manifest['source_counts'].values())} observations**, of which **{selection_counts['eligible_census']}** had usable observation-local text. The eligible census contained **129 Facebook** and **34 Ouedkniss** observations. The evaluation sample has **{len(annotation_examples)} observations**:

| Source | Sampled | Current baseline has mentions | Current baseline has no mentions |
| --- | ---: | ---: | ---: |
{source_rows}
| **Total** | **{len(annotation_examples)}** | **{sum(mention_counts.values())}** | **{len(annotation_examples) - sum(mention_counts.values())}** |

The target follows the 129:34 source proportions with a 47/13 allocation. Adult, abuse-related, or romance-story ads and one-word UNKNOWN fragments were screened out, inherited controls were held out, and exact duplicate text was represented once per source. Only 12 distinct Ouedkniss texts remained; the final sample is **47 Facebook and 12 Ouedkniss observations (59 total)**. No-mention strata include every currently emitted non-product class with usable examples. A deterministic SHA-256 rank with seed `{SEED}` selects rows inside each stratum. This challenge-enriched sample supports error review and is not a production prevalence estimate.

The UNKNOWN stratum includes vehicle-model and price-promotion wording with no deterministic product mention, alongside lifestyle and general advertising copy. These give reviewers material to assess potentially ambiguous cases and possible missed offers; the baseline does not treat them as human labels.

The full eligible census and source selectors were reproduced using the Worker 034 runner. Worker 034 selects Facebook text from the observation's captured card payload and Ouedkniss text from that listing observation's source payload. Mutable entity-level text is not used. The sample records the stable source type and observation reference, selected field path, selector version, and exact-source text hash.

## Exclusions and privacy

The **105 observations without usable observation-local text** were excluded from human labeling: 88 Facebook observations had missing or unusable local copy, and 17 Ouedkniss observations lacked a historical title. No current entity text was substituted. The **18 inherited Worker 034 adjudication controls** were excluded from the sample to keep this new annotation independent of prior labels. Content screening excluded `{selection_counts['screening_exclusion_total']}` candidates (`{json.dumps(selection_counts['screening_exclusions_by_source_and_reason'], ensure_ascii=False)}`) to avoid adult, abuse-related, or romance-story copy and one-word fragments that could be personal names. Exact duplicate local-text candidates were represented once per source (`{json.dumps(selection_counts['duplicate_text_candidates_not_selected_by_source'], ensure_ascii=False)}` repeated-text observations bypassed during selection).

Privacy screening redacted **{redacted_records} evidence records** with **{sum(redaction_counts.values())} evidence spans**: `{json.dumps(dict(sorted(redaction_counts.items())), ensure_ascii=False)}`. Baseline mention span redactions: `{json.dumps(dict(sorted(baseline_redactions.items())), ensure_ascii=False)}`. The packaged evidence was scanned after generation for email addresses, URLs, phone patterns, social handles, and credential/token assignments; the match counts were `{json.dumps(post_scan, ensure_ascii=False)}`. The source database was not changed. Exact unredacted evidence is represented by its SHA-256 hash and provenance path.

## Database integrity and reproduction

- SHA-256 before: `{db_before}`
- SHA-256 after: `{db_after}`
- Unchanged: **{str(db_before == db_after).lower()}**
- Production writes: **none**
- Database access in Worker 035 preparation: file hashing only; no SQLite connection
- Stable sample set SHA-256: `{sample_set_sha256}`

Run from the repository root:

```powershell
.\\.venv\\Scripts\\python.exe normalization\\worker_034_evaluate.py
.\\.venv\\Scripts\\python.exe normalization\\worker_035_prepare.py
```

Worker 034 reproduces the complete read-only census and frozen engine outputs. Worker 035 reads that census and writes only the five package artifacts listed below. The Worker 034 run also verifies its pure output against a temporary isolated persistence replay; schema application and derived writes occur only in that temporary clone.

## Annotation rules

Use the concise rules and examples in `normalization/evaluation/worker_035_reviewer_guide.md`. The annotation sample fields remain null until a human completes review. Use the separate baseline only after annotations are frozen.

## Deliverables

- `reports/worker_035_annotation_package.md`
- `reports/worker_035_results.json`
- `normalization/evaluation/worker_035_annotation_sample.json`
- `normalization/evaluation/worker_035_reviewer_guide.md`
- `normalization/evaluation/worker_035_baseline.json`
"""
    (ROOT / "reports" / "worker_035_annotation_package.md").write_text(report, encoding="utf-8")
    print(json.dumps({
        "sample_size": len(annotation_examples),
        "source_counts": {SOURCE_NAMES[source]: source_counts[source] for source in SOURCE_ORDER},
        "mention_counts": {SOURCE_NAMES[source]: mention_counts[source] for source in SOURCE_ORDER},
        "database_unchanged": db_before == db_after,
        "post_redaction_scan": post_scan,
        "redacted_evidence_records": redacted_records,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
