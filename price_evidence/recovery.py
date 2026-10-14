"""Resumable source-grounded recovery of Ouedkniss evidence from observations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from migration_lifecycle import table_fingerprint
from price_evidence.ouedkniss import (
    EXTRACTION_METHOD,
    EXTRACTION_VERSION,
    assess_ouedkniss_price,
)
from price_evidence.repository import (
    _key,
    append_ouedkniss_price_evidence,
    price_evidence_schema_installed,
)
from schema_validation import price_evidence_trust_boundary_problems


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source_idempotency_key(observation_id: int) -> str:
    return _key({
        "platform": "Ouedkniss",
        "observation_id": observation_id,
        "method": EXTRACTION_METHOD,
        "version": EXTRACTION_VERSION,
        "evidence_index": 0,
    })


def _payload_for_source(row: sqlite3.Row) -> tuple[dict[str, Any] | None, str | None, str | None]:
    try:
        payload = json.loads(row["source_payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        return None, f"source_payload_json is malformed: {exc}", None
    if not isinstance(payload, dict):
        return None, "source_payload_json is not a JSON object", None
    announcement = payload.get("announcement")
    if not isinstance(announcement, dict):
        # Migration 001 records a source snapshot whose zero price was explicitly
        # translated to unknown. The exact immutable observation row has NULL price;
        # no mutable marketplace_listings value is consulted or reconstructed.
        if (
            payload.get("migration") == "001_foundation_v2"
            and payload.get("legacy_price_zero_treated_as_unknown") is True
            and row["price"] is None
        ):
            return {}, None, "Migration 001 snapshot has an explicit unknown-price marker and NULL observation price"
        return None, "exact observation payload has no announcement object", None
    return announcement, None, None


def recover_ouedkniss_price_evidence(
    conn: sqlite3.Connection,
    *,
    extracted_at: str | None = None,
) -> dict[str, Any]:
    """Append evidence using each immutable observation payload and exact row identity.

    The caller must use an autocommit connection. Each evidence insert is its own
    commit so an interrupted run can be resumed without duplicating earlier rows.
    """
    if conn.in_transaction:
        raise RuntimeError("Historical recovery requires an autocommit connection for resumability")
    if not price_evidence_schema_installed(conn):
        raise RuntimeError(
            "Migrations 002, 003, and 004 must be applied and verified before historical recovery: "
            + "; ".join(price_evidence_trust_boundary_problems(conn))
        )
    conn.row_factory = sqlite3.Row
    extracted_at = extracted_at or _now()
    before = table_fingerprint(conn, "marketplace_listing_observations")
    rows = conn.execute(
        """SELECT o.id, o.listing_id, o.observed_at, o.price, o.source_payload_json,
                  l.platform
           FROM marketplace_listing_observations AS o
           JOIN marketplace_listings AS l ON l.id = o.listing_id
           WHERE l.platform = 'Ouedkniss'
           ORDER BY o.id"""
    ).fetchall()

    report: dict[str, Any] = {
        "source_observations": len(rows),
        "imported": 0,
        "skipped": 0,
        "already_present": 0,
        "ambiguous": 0,
        "missing": 0,
        "failed": 0,
        "details": [],
    }
    for row in rows:
        observation_id = int(row["id"])
        announcement, payload_problem, provenance_note = _payload_for_source(row)
        if payload_problem:
            report["skipped"] += 1
            report["details"].append({
                "observation_id": observation_id,
                "status": "skipped",
                "reason": payload_problem,
            })
            continue

        key = _source_idempotency_key(observation_id)
        existing = conn.execute(
            "SELECT id FROM price_evidence WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing is not None:
            report["skipped"] += 1
            report["already_present"] += 1
            report["details"].append({
                "observation_id": observation_id,
                "price_evidence_id": int(existing[0]),
                "status": "skipped",
                "reason": "stable observation/method/version idempotency key already exists",
            })
            continue

        try:
            candidate = assess_ouedkniss_price(
                announcement,
                source_entity_key=str(
                    announcement.get("source_listing_id")
                    or row["listing_id"]
                ),
            )
            evidence_id = append_ouedkniss_price_evidence(
                conn,
                observation_id,
                extracted_at=extracted_at,
            )
            stored_evidence = conn.execute(
                "SELECT quality_status, reason_codes_json FROM price_evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            stored_reasons = json.loads(stored_evidence["reason_codes_json"])
            report["imported"] += 1
            reasons = list(candidate.reason_codes)
            status = "imported"
            if candidate.extraction_status == "MISSING":
                report["missing"] += 1
                status = "missing"
            elif candidate.extraction_status == "SUCCESS" and (
                candidate.currency_basis == "UNKNOWN"
                or candidate.unit_status in ("UNRESOLVED", "UNKNOWN")
                or candidate.quality_status in ("AMBIGUOUS", "UNIT_OR_CURRENCY_UNCONFIRMED")
            ):
                report["ambiguous"] += 1
                status = "ambiguous"
            report["details"].append({
                "observation_id": observation_id,
                "price_evidence_id": evidence_id,
                "source_observed_at": row["observed_at"],
                "source_amount": candidate.raw_value,
                "source_unit": candidate.source_unit,
                "source_type": announcement.get("priceType", announcement.get("price_type")),
                "candidate_amount": candidate.candidate_amount,
                "currency_code": candidate.currency_code,
                "unit_status": candidate.unit_status,
                "quality_status": candidate.quality_status,
                "stored_quality_status": stored_evidence["quality_status"],
                "stored_reason_codes": stored_reasons,
                "extraction_status": candidate.extraction_status,
                "status": status,
                "reason_codes": reasons,
                "provenance_note": provenance_note,
            })
        except Exception as exc:
            report["failed"] += 1
            report["details"].append({
                "observation_id": observation_id,
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
            })

    after = table_fingerprint(conn, "marketplace_listing_observations")
    if before != after:
        raise RuntimeError("Historical recovery changed immutable marketplace observations")

    evidence_counts = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            """SELECT extraction_status, COUNT(*)
               FROM price_evidence WHERE source_platform='Ouedkniss'
               GROUP BY extraction_status ORDER BY extraction_status"""
        )
    }
    suspicious_one_unit = int(conn.execute(
        """SELECT COUNT(*) FROM price_evidence
           WHERE source_platform='Ouedkniss' AND source_unit='UNIT'
             AND candidate_amount=1 AND quality_status='SUSPICIOUS_PLACEHOLDER'"""
    ).fetchone()[0])
    trusted = int(conn.execute(
        "SELECT COUNT(*) FROM trusted_price_evidence WHERE source_platform='Ouedkniss'"
    ).fetchone()[0])
    total_evidence = int(conn.execute(
        "SELECT COUNT(*) FROM price_evidence WHERE source_platform='Ouedkniss'"
    ).fetchone()[0])
    ambiguous_evidence = int(conn.execute(
        """SELECT COUNT(*) FROM price_evidence
           WHERE source_platform='Ouedkniss' AND extraction_status='SUCCESS'
             AND (currency_basis='UNKNOWN' OR unit_status IN ('UNRESOLVED','UNKNOWN')
                  OR quality_status IN ('AMBIGUOUS','UNIT_OR_CURRENCY_UNCONFIRMED',
                                        'SUSPICIOUS_PLACEHOLDER'))"""
    ).fetchone()[0])
    report["source_observation_fingerprint_before"] = before["fingerprint"]
    report["source_observation_fingerprint_after"] = after["fingerprint"]
    report["source_observation_id_fingerprint_before"] = before["id_fingerprint"]
    report["source_observation_id_fingerprint_after"] = after["id_fingerprint"]
    report["source_observations_unchanged"] = before == after
    report["evidence_counts_by_extraction_status"] = evidence_counts
    report["successful_candidate_rows"] = evidence_counts.get("SUCCESS", 0)
    report["missing_evidence_rows"] = evidence_counts.get("MISSING", 0)
    report["ambiguous_evidence_rows"] = ambiguous_evidence
    report["suspicious_one_unit_rows"] = suspicious_one_unit
    report["trusted_price_rows"] = trusted
    report["untrusted_evidence_rows"] = total_evidence - trusted
    report["eligible_source_observations"] = int(conn.execute(
        """SELECT COUNT(*) FROM price_evidence
           WHERE source_platform='Ouedkniss' AND extraction_status='SUCCESS'
             AND candidate_amount IS NOT NULL"""
    ).fetchone()[0])
    return report


__all__ = ["recover_ouedkniss_price_evidence"]
