from __future__ import annotations

import base64
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse
from decimal import Decimal, InvalidOperation

from price_evidence.facebook import LandingPageExtraction, extract_landing_page_prices
from price_evidence.currency import normalize_currency_code
from price_evidence.models import PriceCandidate
from price_evidence.ouedkniss import (
    EXTRACTION_METHOD as OUEDKNISS_METHOD,
    EXTRACTION_VERSION as OUEDKNISS_VERSION,
    assess_ouedkniss_price,
)
from migration_lifecycle import _sql_statements
from schema_validation import (
    PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS,
    PRICE_EVIDENCE_REQUIRED_OBJECTS,
    price_evidence_currency_policy_problems,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "002_price_evidence.sql"
TRUST_POLICY_MIGRATION_PATH = ROOT / "migrations" / "003_price_evidence_trust_policy.sql"
MAX_CANDIDATE_AMOUNT = 1_000_000_000_000.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _key(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _validated_amount(value: Any, label: str) -> float | None:
    """Accept only finite numeric positive prices within the documented policy."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be a finite numeric value, not {type(value).__name__}")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a finite numeric value") from None
    if not decimal_value.is_finite():
        raise ValueError(f"{label} must be finite")
    if decimal_value <= 0 or decimal_value > Decimal(str(MAX_CANDIDATE_AMOUNT)):
        raise ValueError(f"{label} must be greater than 0 and no greater than 1e12")
    number = float(decimal_value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _bounded_extracted_candidate(candidate: PriceCandidate) -> PriceCandidate:
    """Keep source text while refusing an invalid parsed amount as structured data."""
    reasons = list(candidate.reason_codes)
    try:
        amount = _validated_amount(candidate.candidate_amount, "extracted candidate amount")
        amount_max = _validated_amount(candidate.candidate_amount_max, "extracted candidate maximum")
        if amount_max is not None and (amount is None or amount_max < amount):
            raise ValueError("extracted candidate maximum must be at least its amount")
        return replace(candidate, candidate_amount=amount, candidate_amount_max=amount_max)
    except ValueError:
        if "PRICE_AMOUNT_OUTSIDE_POLICY" not in reasons:
            reasons.append("PRICE_AMOUNT_OUTSIDE_POLICY")
        return replace(
            candidate,
            candidate_amount=None,
            candidate_amount_max=None,
            extraction_status="ERROR",
            quality_status="EXTRACTION_ERROR",
            reason_codes=tuple(reasons),
        )


def _review_signing_key() -> bytes:
    value = os.environ.get("KRATZWERK_PRICE_REVIEW_HMAC_KEY")
    if not value:
        raise ValueError(
            "Approved price-evidence reviews require an explicitly configured review signing key"
        )
    key = value.encode("utf-8")
    if len(key) < 32:
        raise ValueError("The price-evidence review signing key must contain at least 32 UTF-8 bytes")
    return key


def price_evidence_review_authorization_payload(
    price_evidence_id: int,
    *,
    decision: str,
    reviewer: str,
    reviewed_at: str,
    quality_status: str | None = None,
    currency_code: str | None = None,
    currency_basis: str | None = None,
    unit_status: str | None = None,
    product_identity_state: str | None = None,
    price_type: str | None = None,
    product_identity_key: str | None = None,
    unit_interpretation: str | None = None,
    reason_codes: tuple[str, ...] = (),
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical fields an authorized reviewer must sign for approval."""
    return {
        "purpose": "kratzwerk-price-evidence-review-v1",
        "price_evidence_id": price_evidence_id,
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "quality_status": quality_status,
        "currency_code": currency_code,
        "currency_basis": currency_basis,
        "unit_status": unit_status,
        "product_identity_state": product_identity_state,
        "product_identity_key": product_identity_key,
        "unit_interpretation": unit_interpretation,
        "price_type": price_type,
        "reason_codes": list(reason_codes),
        "evidence": dict(evidence or {}),
    }


def _signature_for_payload(key: bytes, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _review_authorization_udf(key: bytes):
    """Build the SQLite trigger verifier for one explicit review signing key."""
    def verify(*values: Any) -> int:
        try:
            (evidence_id, decision, quality, currency, basis, unit, identity_state,
             identity_key, unit_interpretation, price_type, reviewer, reviewed_at,
             reasons_json, evidence_json) = values
            reasons = json.loads(reasons_json or "[]")
            stored_evidence = json.loads(evidence_json or "{}")
            authorization = stored_evidence.pop("review_authorization", None)
            if not isinstance(authorization, dict):
                return 0
            signature = authorization.get("signature")
            if not isinstance(signature, str):
                return 0
            payload = price_evidence_review_authorization_payload(
                int(evidence_id), decision=decision, reviewer=reviewer, reviewed_at=reviewed_at,
                quality_status=quality, currency_code=currency, currency_basis=basis,
                unit_status=unit, product_identity_state=identity_state,
                product_identity_key=identity_key, unit_interpretation=unit_interpretation,
                price_type=price_type, reason_codes=tuple(reasons), evidence=stored_evidence,
            )
            expected = _signature_for_payload(key, payload)
            expected_key_id = hashlib.sha256(key).hexdigest()[:16]
            return int(
                authorization.get("key_id") == expected_key_id
                and hmac.compare_digest(signature, expected)
            )
        except (TypeError, ValueError, json.JSONDecodeError, OverflowError):
            return 0
    return verify


def _has_currency_evidence(
    evidence: dict[str, Any], currency_code: str, *, original_url: str | None,
    original_sha256: str | None,
) -> bool:
    sources = evidence.get("currency_evidence")
    if not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("source_url")
        content = source.get("content")
        claimed_hash = source.get("content_sha256")
        if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
            continue
        if not isinstance(content, str) or not content:
            continue
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if claimed_hash != actual_hash or source.get("currency_code") != currency_code:
            continue
        if original_url and url.rstrip("/").casefold() == original_url.rstrip("/").casefold():
            continue
        if original_sha256 and actual_hash == original_sha256:
            continue
        if currency_code == "DZD":
            explicit = re.search(r"(?<![A-Za-z])DZD(?![A-Za-z])|\bDA\b|د\s*ج|د\.ج", content, re.I)
        else:
            explicit = re.search(rf"(?<![A-Za-z]){currency_code}(?![A-Za-z])", content, re.I)
        if explicit:
            return True
    return False


def price_evidence_schema_installed(conn: sqlite3.Connection) -> bool:
    return not price_evidence_currency_policy_problems(conn)


def apply_price_evidence_migration(conn: sqlite3.Connection) -> None:
    """Test-fixture helper; production migrations must use the backup-aware runner."""
    if price_evidence_schema_installed(conn):
        return
    objects = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    all_002_names = {
        name for names in PRICE_EVIDENCE_REQUIRED_OBJECTS.values() for name in names
    } | {name for names in PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS.values() for name in names}
    remnants = sorted(objects & all_002_names)
    if remnants:
        raise RuntimeError(
            "Migration 002/003 is partially installed; the fixture helper will not delete or replace "
            f"objects: {remnants}"
        )
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"marketplace_listing_observations", "facebook_ad_observations", "facebook_ads"}
    missing = required - tables
    if missing:
        raise RuntimeError(f"Foundation V2 schema required; missing {sorted(missing)}")
    if conn.in_transaction:
        raise RuntimeError("Commit the fixture setup before applying Migration 002")
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement must be enabled before Migration 002")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _sql_statements(MIGRATION_PATH.read_text(encoding="utf-8")):
            conn.execute(statement)
        for statement in _sql_statements(TRUST_POLICY_MIGRATION_PATH.read_text(encoding="utf-8")):
            conn.execute(statement)
        problems = price_evidence_currency_policy_problems(conn)
        if problems:
            raise RuntimeError("Price Evidence schema is incomplete: " + "; ".join(problems))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Price Evidence migration has foreign-key violations: {violations[:5]}")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Price Evidence migration failed integrity_check")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _source_observation(conn: sqlite3.Connection, observation_id: int) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT o.id, o.listing_id, o.observed_at, o.price, o.source_payload_json,
                  l.platform
           FROM marketplace_listing_observations AS o
           JOIN marketplace_listings AS l ON l.id = o.listing_id
           WHERE o.id = ?""",
        (observation_id,),
    ).fetchone()
    if row is None or row["platform"] != "Ouedkniss":
        raise ValueError(f"Not an Ouedkniss marketplace observation: {observation_id}")
    return row


def _is_repeated_one_unit_pattern(
    conn: sqlite3.Connection, observed_at: str
) -> bool:
    rows = conn.execute(
        """SELECT o.listing_id, o.source_payload_json
           FROM marketplace_listing_observations AS o
           JOIN marketplace_listings AS l ON l.id = o.listing_id
           WHERE l.platform = 'Ouedkniss' AND o.observed_at = ?""",
        (observed_at,),
    ).fetchall()
    matching_listing_ids: set[int] = set()
    for row in rows:
        try:
            payload = json.loads(row["source_payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        announcement = payload.get("announcement") if isinstance(payload, dict) else None
        if not isinstance(announcement, dict):
            continue
        amount = announcement.get("price")
        if isinstance(amount, bool) or amount != 1 or announcement.get("price_unit") != "UNIT":
            continue
        matching_listing_ids.add(int(row["listing_id"]))
    return len(matching_listing_ids) >= 3


def append_ouedkniss_price_evidence(
    conn: sqlite3.Connection,
    observation_id: int,
    *,
    extracted_at: str | None = None,
    repeated_one_unit_pattern: bool | None = None,
) -> int:
    """Derive one immutable interpretation linked to one exact source observation."""
    if not price_evidence_schema_installed(conn):
        problems = price_evidence_currency_policy_problems(conn)
        raise RuntimeError(
            "Migration 002/003 (price evidence trust policy) is missing or incomplete: " + "; ".join(problems)
        )
    source = _source_observation(conn, observation_id)
    try:
        payload = json.loads(source["source_payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        payload = {}
    announcement = payload.get("announcement") if isinstance(payload, dict) else None
    if not isinstance(announcement, dict):
        announcement = {}
    source_listing_id = announcement.get("source_listing_id")
    if not isinstance(source_listing_id, (str, int)) or not str(source_listing_id).strip():
        source_listing_id = payload.get("source_listing_id") if isinstance(payload, dict) else None
    entity_key = str(source_listing_id).strip() if source_listing_id is not None else str(source["listing_id"])
    source_url = announcement.get("url")
    if not isinstance(source_url, str) or not source_url.strip():
        source_url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(source_url, str) or not source_url.strip():
        source_url = None
    pattern = (
        _is_repeated_one_unit_pattern(conn, source["observed_at"])
        if repeated_one_unit_pattern is None
        else repeated_one_unit_pattern
    )
    candidate = _bounded_extracted_candidate(assess_ouedkniss_price(
        announcement,
        source_entity_key=entity_key,
        repeated_one_unit_pattern=pattern,
    ))
    extracted_at = extracted_at or _utc_now()
    idempotency_key = _key({
        "platform": "Ouedkniss",
        "observation_id": observation_id,
        "method": OUEDKNISS_METHOD,
        "version": OUEDKNISS_VERSION,
        "evidence_index": 0,
    })
    conn.execute(
        """INSERT INTO price_evidence (
            source_platform, source_entity_key, marketplace_observation_id,
            facebook_landing_page_fetch_id,
               source_observed_at, price_observed_at, original_url, resolved_url,
               raw_value_json, raw_price_text, candidate_amount, candidate_amount_max,
               source_unit, unit_status, currency_code, currency_basis, price_type,
               product_identity_state, product_identity_key, extraction_method, extraction_version, extracted_at,
               extraction_status, quality_status, reason_codes_json,
               supporting_evidence_json, evidence_index, idempotency_key
           ) VALUES (
               'Ouedkniss', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, ?
           ) ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            entity_key,
            observation_id,
            source["observed_at"],
            source["observed_at"],
            source_url,
            source_url,
            _json(candidate.raw_value),
            candidate.raw_price_text,
            candidate.candidate_amount,
            candidate.candidate_amount_max,
            candidate.source_unit,
            candidate.unit_status,
            candidate.currency_code,
            candidate.currency_basis,
            candidate.price_type,
            candidate.product_identity_state,
            OUEDKNISS_METHOD,
            OUEDKNISS_VERSION,
            extracted_at,
            candidate.extraction_status,
            candidate.quality_status,
            _json(list(candidate.reason_codes)),
            _json(candidate.evidence),
            idempotency_key,
        ),
    )
    row = conn.execute(
        "SELECT id FROM price_evidence WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return int(row[0])


def append_ouedkniss_evidence_for_capture(
    conn: sqlite3.Connection, observed_at: str, *, extracted_at: str | None = None
) -> int:
    """Derive evidence for all exact-time Ouedkniss source rows, idempotently."""
    if not price_evidence_schema_installed(conn):
        problems = price_evidence_currency_policy_problems(conn)
        raise RuntimeError(
            "Migrations 002/003 (price evidence trust policy) are missing or incomplete: "
            + "; ".join(problems)
        )
    rows = conn.execute(
        """SELECT o.id
           FROM marketplace_listing_observations AS o
           JOIN marketplace_listings AS l ON l.id = o.listing_id
           WHERE l.platform = 'Ouedkniss' AND o.observed_at = ?
           ORDER BY o.id""",
        (observed_at,),
    ).fetchall()
    for row in rows:
        append_ouedkniss_price_evidence(
            conn, int(row[0]), extracted_at=extracted_at
        )
    return len(rows)


def landing_url_from_ad_observation(conn: sqlite3.Connection, observation_id: int) -> str | None:
    """Read a destination only from the exact ad observation, never the mutable ad entity."""
    row = conn.execute(
        "SELECT detected_metadata_json FROM facebook_ad_observations WHERE id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown Facebook ad observation: {observation_id}")
    try:
        metadata = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    value = metadata.get("landing_url") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def append_landing_page_fetch(
    conn: sqlite3.Connection,
    facebook_ad_observation_id: int,
    *,
    fetched_at: str,
    fetch_status: str,
    content: str | bytes | None = None,
    original_url: str | None = None,
    resolved_url: str | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
    fetcher_name: str = "offline_fixture",
    fetcher_version: str = "v1",
    evidence: dict[str, Any] | None = None,
) -> int:
    """Append supplied fetch metadata; this helper never performs a network request."""
    if not price_evidence_schema_installed(conn):
        raise RuntimeError("Apply migrations 002 and 003 before recording landing-page fetches")
    observed_destination = landing_url_from_ad_observation(conn, facebook_ad_observation_id)
    if not observed_destination:
        raise ValueError("The exact Facebook observation has no destination URL")
    if original_url is not None and original_url != observed_destination:
        raise ValueError("Fetch original_url must match the destination on the exact Facebook observation")
    original_url = observed_destination
    if fetch_status not in {"FETCHED", "INACCESSIBLE", "FAILED"}:
        raise ValueError(f"Unsupported fetch status: {fetch_status}")
    if content is not None and not isinstance(content, (str, bytes)):
        raise TypeError("content must be UTF-8 text, bytes, or None")
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content
    content_sha256 = hashlib.sha256(content_bytes).hexdigest() if content_bytes is not None else None
    key = _key({
        "observation_id": facebook_ad_observation_id,
        "original_url": original_url,
        "resolved_url": resolved_url,
        "fetched_at": fetched_at,
        "fetch_status": fetch_status,
        "content_sha256": content_sha256,
        "fetcher_name": fetcher_name,
        "fetcher_version": fetcher_version,
    })
    conn.execute(
        """INSERT INTO facebook_landing_page_fetches (
               facebook_ad_observation_id, original_url, resolved_url, fetched_at,
               fetch_status, http_status, content_type, content_sha256,
               fetcher_name, fetcher_version, evidence_json, idempotency_key
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            facebook_ad_observation_id, original_url, resolved_url, fetched_at,
            fetch_status, http_status, content_type, content_sha256,
            fetcher_name, fetcher_version,
            _json({
                "fetcher_metadata": evidence or {"network_io": False},
                "raw_source_content": ({
                    "encoding": "base64",
                    "bytes": base64.b64encode(content_bytes).decode("ascii"),
                } if content_bytes is not None else None),
            }),
            key,
        ),
    )
    return int(conn.execute(
        "SELECT id FROM facebook_landing_page_fetches WHERE idempotency_key = ?", (key,)
    ).fetchone()[0])


def append_facebook_price_evidence(
    conn: sqlite3.Connection,
    landing_page_fetch_id: int,
    extraction: LandingPageExtraction,
    *,
    extracted_at: str | None = None,
) -> list[int]:
    """Re-extract and persist evidence from the exact hash-checked fetched content.

    Caller-constructed candidates are not provenance. Their amount fields are
    validated as input, but currency, basis, verification labels, metadata,
    extraction method, and version are re-derived from the linked fetch content.
    """
    if not price_evidence_schema_installed(conn):
        raise RuntimeError("Apply migrations 002 and 003 before recording price evidence")
    if type(extraction) is not LandingPageExtraction:
        raise TypeError("extraction must be a LandingPageExtraction")
    if not isinstance(extraction.candidates, (tuple, list)):
        raise TypeError("extraction candidates must be a tuple or list")
    for candidate in extraction.candidates:
        if type(candidate) is not PriceCandidate:
            raise TypeError("extraction candidates must be PriceCandidate objects")
        claimed_amount = _validated_amount(candidate.candidate_amount, "candidate_amount")
        claimed_max = _validated_amount(candidate.candidate_amount_max, "candidate_amount_max")
        if claimed_max is not None and (claimed_amount is None or claimed_max < claimed_amount):
            raise ValueError("candidate_amount_max must be at least candidate_amount")

    source = conn.execute(
        """SELECT f.*, o.observed_at AS ad_observed_at, a.library_id, a.id AS ad_id
           FROM facebook_landing_page_fetches AS f
           JOIN facebook_ad_observations AS o ON o.id = f.facebook_ad_observation_id
           JOIN facebook_ads AS a ON a.id = o.ad_id
           WHERE f.id = ?""",
        (landing_page_fetch_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Unknown landing-page fetch: {landing_page_fetch_id}")
    extracted_at = extracted_at or _utc_now()
    source_entity_key = str(source["library_id"] or source["ad_id"])
    raw_content: bytes | None = None
    content_problem: str | None = None
    try:
        fetch_record = json.loads(source["evidence_json"] or "{}")
        content_record = fetch_record.get("raw_source_content") if isinstance(fetch_record, dict) else None
        if source["fetch_status"] != "FETCHED":
            content_problem = "LANDING_PAGE_NOT_FETCHED"
        elif not isinstance(content_record, dict) or content_record.get("encoding") != "base64":
            content_problem = "FETCH_SOURCE_CONTENT_MISSING"
        else:
            raw_content = base64.b64decode(content_record.get("bytes", ""), validate=True)
            actual_hash = hashlib.sha256(raw_content).hexdigest()
            if not source["content_sha256"] or actual_hash != source["content_sha256"]:
                raw_content = None
                content_problem = "FETCH_SOURCE_CONTENT_HASH_MISMATCH"
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_content = None
        content_problem = "FETCH_SOURCE_CONTENT_UNREADABLE"

    if raw_content is not None:
        try:
            html = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            html = None
            content_problem = "FETCH_SOURCE_CONTENT_NOT_UTF8"
    else:
        html = None
    derived = extract_landing_page_prices(
        html,
        source_entity_key=source_entity_key,
        fetch_status=(source["fetch_status"] if content_problem is None else "FETCHED"),
    )
    candidates = [_bounded_extracted_candidate(candidate) for candidate in derived.candidates]
    if content_problem:
        candidates = [replace(
            candidate,
            currency_code=None,
            currency_basis="UNKNOWN",
            candidate_amount=None,
            candidate_amount_max=None,
            extraction_status="ERROR",
            quality_status="EXTRACTION_ERROR",
            reason_codes=tuple(dict.fromkeys((*candidate.reason_codes, content_problem))),
        ) for candidate in candidates]

    written: list[int] = []
    for index, candidate in enumerate(candidates):
        key = _key({
            "landing_page_fetch_id": landing_page_fetch_id,
            "method": derived.method,
            "version": derived.version,
            "candidate_index": index,
            "product_key": candidate.product_key,
        })
        conn.execute(
            """INSERT INTO price_evidence (
                   source_platform, source_entity_key, marketplace_observation_id,
                   facebook_landing_page_fetch_id, source_observed_at, price_observed_at,
                   original_url, resolved_url, raw_value_json, raw_price_text,
                   candidate_amount, candidate_amount_max, source_unit, unit_status,
                   currency_code, currency_basis, price_type, product_identity_state, product_identity_key,
                   extraction_method, extraction_version, extracted_at, extraction_status,
                   quality_status, reason_codes_json, supporting_evidence_json,
                   evidence_index, idempotency_key
               ) VALUES (
                   'facebook', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               ) ON CONFLICT(idempotency_key) DO NOTHING""",
            (
                source_entity_key,
                landing_page_fetch_id,
                source["ad_observed_at"],
                source["fetched_at"],
                source["original_url"],
                source["resolved_url"],
                _json(candidate.raw_value),
                candidate.raw_price_text,
                candidate.candidate_amount,
                candidate.candidate_amount_max,
                candidate.source_unit,
                candidate.unit_status,
                candidate.currency_code,
                candidate.currency_basis,
                candidate.price_type,
                candidate.product_identity_state,
                candidate.product_key,
                derived.method,
                derived.version,
                extracted_at,
                candidate.extraction_status,
                candidate.quality_status,
                _json(list(candidate.reason_codes)),
                _json({
                    "fetch_provenance": {
                        "landing_page_fetch_id": landing_page_fetch_id,
                        "content_sha256": source["content_sha256"],
                        "content_hash_verified": raw_content is not None,
                        "content_issue": content_problem,
                        "fetch_status": source["fetch_status"],
                        "original_url": source["original_url"],
                        "resolved_url": source["resolved_url"],
                        "fetcher_name_claim": source["fetcher_name"],
                        "fetcher_version_claim": source["fetcher_version"],
                    },
                    "extraction_summary": derived.evidence,
                    "candidate": candidate.evidence,
                }),
                index,
                key,
            ),
        )
        evidence_id = conn.execute(
            "SELECT id FROM price_evidence WHERE idempotency_key = ?", (key,)
        ).fetchone()[0]
        written.append(int(evidence_id))
    return written


def append_price_evidence_review(
    conn: sqlite3.Connection,
    price_evidence_id: int,
    *,
    decision: str,
    reviewer: str,
    reviewed_at: str,
    quality_status: str | None = None,
    currency_code: str | None = None,
    currency_basis: str | None = None,
    unit_status: str | None = None,
    product_identity_state: str | None = None,
    price_type: str | None = None,
    product_identity_key: str | None = None,
    unit_interpretation: str | None = None,
    reason_codes: tuple[str, ...] = (),
    evidence: dict[str, Any] | None = None,
    authorization_signature: str | None = None,
    idempotency_key: str | None = None,
) -> int:
    """Append a review; only an explicitly signed authorization can approve it.

    The signer HMAC covers every decision field and the complete review evidence.
    Currency overrides additionally require hash-checked source content that states
    the same supported currency. The SQLite insert trigger independently verifies
    the authorization so direct approved writes fail closed on ordinary connections.
    """
    review_reasons = list(reason_codes)
    if evidence is not None and not isinstance(evidence, dict):
        raise TypeError("review evidence must be a dictionary")
    review_evidence = dict(evidence or {})
    if "review_authorization" in review_evidence:
        raise ValueError("review_authorization is reserved for the repository")
    if currency_basis not in (None, "SOURCE_CONFIRMED", "INDEPENDENTLY_VERIFIED", "UNKNOWN"):
        raise ValueError(f"Unsupported currency basis: {currency_basis!r}")
    recognized_code = normalize_currency_code(currency_code)
    if currency_code is not None and recognized_code is None:
        # Preserve raw reviewer input while keeping the structured code unconfirmed.
        try:
            json.dumps(currency_code)
            raw_currency = currency_code
        except (TypeError, ValueError):
            raw_currency = repr(currency_code)
        review_evidence.setdefault("unrecognized_currency_code_raw", raw_currency)
        if "CURRENCY_CODE_UNRECOGNIZED" not in review_reasons:
            review_reasons.append("CURRENCY_CODE_UNRECOGNIZED")
        currency_code = None
        currency_basis = "UNKNOWN"
    elif recognized_code is not None:
        currency_code = recognized_code
    elif currency_basis in ("SOURCE_CONFIRMED", "INDEPENDENTLY_VERIFIED"):
        currency_basis = "UNKNOWN"
        if "CURRENCY_CODE_MISSING" not in review_reasons:
            review_reasons.append("CURRENCY_CODE_MISSING")

    if decision == "APPROVED":
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("Approved reviews require a non-empty reviewer identity")
        if not isinstance(authorization_signature, str):
            raise ValueError("APPROVED requires an authorized review signature")
    elif authorization_signature is not None:
        raise ValueError("Review authorization signatures are only valid for APPROVED decisions")

    source = conn.execute(
        """SELECT e.currency_code, e.currency_basis, e.original_url,
                  f.content_sha256, e.source_platform
           FROM price_evidence AS e
           LEFT JOIN facebook_landing_page_fetches AS f
             ON f.id = e.facebook_landing_page_fetch_id
           WHERE e.id = ?""",
        (price_evidence_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Unknown price evidence id: {price_evidence_id}")

    if currency_code is not None:
        source_confirmed = (
            currency_basis == "SOURCE_CONFIRMED"
            and source["currency_code"] == currency_code
            and source["currency_basis"] == "SOURCE_CONFIRMED"
        )
        independently_verified = (
            currency_basis == "INDEPENDENTLY_VERIFIED"
            and _has_currency_evidence(
                review_evidence,
                currency_code,
                original_url=source["original_url"],
                original_sha256=source["content_sha256"],
            )
        )
        if not source_confirmed and not independently_verified:
            if decision == "APPROVED":
                raise ValueError(
                    "Approved currency is not substantiated by the extracted source or signed independent evidence"
                )
            review_evidence.setdefault("unverified_currency_input", currency_code)
            review_reasons.append("CURRENCY_PROVENANCE_UNVERIFIED")
            currency_code = None
            currency_basis = "UNKNOWN"

    signing_key: bytes | None = None
    if decision == "APPROVED":
        signing_key = _review_signing_key()
        payload = price_evidence_review_authorization_payload(
            price_evidence_id,
            decision=decision,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            quality_status=quality_status,
            currency_code=currency_code,
            currency_basis=currency_basis,
            unit_status=unit_status,
            product_identity_state=product_identity_state,
            product_identity_key=product_identity_key,
            unit_interpretation=unit_interpretation,
            price_type=price_type,
            reason_codes=tuple(review_reasons),
            evidence=review_evidence,
        )
        expected_signature = _signature_for_payload(signing_key, payload)
        if not hmac.compare_digest(authorization_signature, expected_signature):
            raise ValueError("Approved review signature is invalid for these fields and evidence")
        review_evidence["review_authorization"] = {
            "scheme": "hmac-sha256-v1",
            "key_id": hashlib.sha256(signing_key).hexdigest()[:16],
            "signature": authorization_signature,
        }
        conn.create_function(
            "kratzwerk_price_review_authorized",
            14,
            _review_authorization_udf(signing_key),
        )
    else:
        # SQLite resolves trigger functions while preparing INSERT statements,
        # even when the trigger WHEN predicate will be false.
        conn.create_function("kratzwerk_price_review_authorized", 14, lambda *_: 0)

    key = idempotency_key or _key({
        "evidence_id": price_evidence_id,
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "quality_status": quality_status,
        "currency_code": currency_code,
        "currency_basis": currency_basis,
        "unit_status": unit_status,
        "product_identity_state": product_identity_state,
        "price_type": price_type,
        "product_identity_key": product_identity_key,
        "unit_interpretation": unit_interpretation,
        "reason_codes": review_reasons,
        "evidence": review_evidence,
    })
    conn.execute(
        """INSERT INTO price_evidence_reviews (
               price_evidence_id, decision, quality_status, currency_code,
               currency_basis, unit_status, product_identity_state, product_identity_key,
               unit_interpretation, price_type,
               reviewer, reviewed_at, reason_codes_json, supporting_evidence_json,
               idempotency_key
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            price_evidence_id, decision, quality_status, currency_code,
            currency_basis, unit_status, product_identity_state,
            product_identity_key, unit_interpretation, price_type,
            reviewer, reviewed_at, _json(review_reasons), _json(review_evidence), key,
        ),
    )
    return int(conn.execute(
        "SELECT id FROM price_evidence_reviews WHERE idempotency_key = ?", (key,)
    ).fetchone()[0])

