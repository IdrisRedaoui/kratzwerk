"""Indexed exact-alias candidate generation and conservative rule evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Iterable

from .extract import ProductMention
from .text import NormalizationProfile, normalize_text


@dataclass(frozen=True)
class AliasEvidence:
    alias_id: int
    source_type: str
    raw_alias: str
    normalized_alias: str


@dataclass(frozen=True)
class CandidateProposal:
    product_id: int
    canonical_name: str
    category: str | None
    attributes: dict[str, Any]
    aliases: tuple[AliasEvidence, ...]


@dataclass(frozen=True)
class MatchEvaluation:
    state: str
    candidate_product_id: int | None
    confidence_label: str
    decision_method: str
    reason: str
    conflicts: tuple[str, ...] = ()


_GENERIC_ONLY_ALIASES = {
    "compressor", "compresseur", "paint", "peinture", "accessory",
    "accessories", "accessoire", "accessoires", "kit", "phone", "computer",
}


def find_exact_alias_candidates(
    conn: sqlite3.Connection,
    mention: ProductMention,
    *,
    source_type: str,
) -> tuple[CandidateProposal, ...]:
    """Use the existing `(source_type, normalized_alias)` index; never scan products.

    Product owners can put exact normalized canonical names into `product_aliases`
    to make them available through the same indexed lookup. Category-only aliases
    are retained as weak review candidates by the evaluator below.
    """
    alias_sources = (source_type, "manual") if source_type != "manual" else ("manual",)
    by_product: dict[int, dict[str, Any]] = {}
    for alias_source in alias_sources:
        rows = conn.execute(
            """SELECT a.id AS alias_id, a.source_type, a.raw_alias, a.normalized_alias,
                      p.id AS product_id, p.canonical_name, p.category, p.attributes_json
               FROM product_aliases AS a INDEXED BY ix_product_alias_lookup
               JOIN products AS p ON p.id = a.product_id
               WHERE a.source_type = ? AND a.normalized_alias = ?
               ORDER BY p.id, a.id""",
            (alias_source, mention.normalized_text),
        ).fetchall()
        for row in rows:
            product_id = int(row["product_id"])
            candidate = by_product.setdefault(product_id, {
                "canonical_name": row["canonical_name"],
                "category": row["category"],
                "attributes": _json_object(row["attributes_json"]),
                "aliases": [],
            })
            candidate["aliases"].append(AliasEvidence(
                alias_id=int(row["alias_id"]),
                source_type=str(row["source_type"]),
                raw_alias=str(row["raw_alias"]),
                normalized_alias=str(row["normalized_alias"]),
            ))
    proposals = [
        CandidateProposal(
            product_id=product_id,
            canonical_name=value["canonical_name"],
            category=value["category"],
            attributes=value["attributes"],
            aliases=tuple(sorted(value["aliases"], key=lambda item: (item.source_type, item.alias_id))),
        )
        for product_id, value in by_product.items()
    ]
    return tuple(sorted(proposals, key=lambda item: item.product_id))


def evaluate_candidates(
    mention: ProductMention,
    candidates: Iterable[CandidateProposal],
    *,
    profile: NormalizationProfile,
) -> MatchEvaluation:
    proposals = tuple(candidates)
    if not proposals:
        return MatchEvaluation(
            state="UNMAPPED", candidate_product_id=None, confidence_label="NO_MATCH",
            decision_method="no_exact_alias_v1", reason="no_exact_alias_candidate",
        )
    if len(proposals) > 1:
        return MatchEvaluation(
            state="REVIEW", candidate_product_id=proposals[0].product_id,
            confidence_label="UNCERTAIN", decision_method="ambiguous_exact_alias_v1",
            reason=f"{len(proposals)} products share the exact normalized alias",
        )

    candidate = proposals[0]
    conflicts = _attribute_conflicts(mention, candidate, profile)
    if conflicts:
        return MatchEvaluation(
            state="REVIEW", candidate_product_id=candidate.product_id,
            confidence_label="UNCERTAIN", decision_method="conflict_review_v1",
            reason="candidate conflicts with explicit mention attributes",
            conflicts=tuple(conflicts),
        )
    if mention.normalized_text in _GENERIC_ONLY_ALIASES:
        return MatchEvaluation(
            state="REVIEW", candidate_product_id=candidate.product_id,
            confidence_label="UNCERTAIN", decision_method="generic_alias_review_v1",
            reason="category-only alias is weak identifying evidence",
        )
    return MatchEvaluation(
        state="MATCHED", candidate_product_id=candidate.product_id,
        confidence_label="HIGH", decision_method="unique_exact_alias_v1",
        reason="one exact normalized alias candidate with no explicit attribute conflict",
    )


def catalog_snapshot_hash(candidates: Iterable[CandidateProposal]) -> str:
    payload = []
    for candidate in candidates:
        payload.append({
            "product_id": candidate.product_id,
            "canonical_name": candidate.canonical_name,
            "category": candidate.category,
            "attributes": candidate.attributes,
            "aliases": [
                {
                    "id": alias.alias_id,
                    "source_type": alias.source_type,
                    "raw_alias": alias.raw_alias,
                    "normalized_alias": alias.normalized_alias,
                }
                for alias in candidate.aliases
            ],
        })
    return _sha256_json(payload)


def candidate_input_hash(
    mention: ProductMention,
    candidates: Iterable[CandidateProposal],
    *,
    matching_version: str,
) -> str:
    return _sha256_json({
        "matching_version": matching_version,
        "mention": {
            "text": mention.normalized_text,
            "brand": mention.brand,
            "model": mention.model,
            "category": mention.category,
            "variant": mention.variant,
            "quantity_value": mention.quantity_value,
            "quantity_unit": mention.quantity_unit,
            "capacity_value": mention.capacity_value,
            "capacity_unit": mention.capacity_unit,
        },
        "catalog_snapshot_hash": catalog_snapshot_hash(candidates),
    })


def _attribute_conflicts(
    mention: ProductMention,
    candidate: CandidateProposal,
    profile: NormalizationProfile,
) -> list[str]:
    attrs = candidate.attributes
    conflicts: list[str] = []
    _compare_text_attribute("brand", mention.brand, attrs.get("brand"), profile, conflicts)
    _compare_text_attribute("model", mention.model, attrs.get("model"), profile, conflicts)
    _compare_text_attribute("variant", mention.variant, attrs.get("variant"), profile, conflicts)

    product_category = candidate.category or attrs.get("category")
    if mention.category and isinstance(product_category, str):
        if normalize_text(mention.category, profile).normalized_text != normalize_text(
            product_category, profile,
        ).normalized_text:
            conflicts.append(f"category_conflict:{mention.category}|{product_category}")

    product_capacity = attrs.get("capacity")
    if mention.capacity_value is not None and product_capacity is not None:
        mention_spec = f"{mention.capacity_value:g}{mention.capacity_unit or ''}"
        product_spec = str(product_capacity).replace(" ", "")
        if normalize_text(mention_spec, profile).normalized_text != normalize_text(
            product_spec, profile,
        ).normalized_text:
            conflicts.append(f"capacity_conflict:{mention_spec}|{product_spec}")
    return conflicts


def _compare_text_attribute(
    name: str,
    mention_value: str | None,
    product_value: Any,
    profile: NormalizationProfile,
    conflicts: list[str],
) -> None:
    if not mention_value or not isinstance(product_value, str) or not product_value.strip():
        return
    mention_normalized = normalize_text(mention_value, profile).normalized_text
    product_normalized = normalize_text(product_value, profile).normalized_text
    if mention_normalized != product_normalized:
        conflicts.append(f"{name}_conflict:{mention_value}|{product_value}")


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
