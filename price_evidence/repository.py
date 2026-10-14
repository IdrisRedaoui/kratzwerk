from __future__ import annotations

import base64
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlsplit
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
    PRICE_EVIDENCE_TRUST_BOUNDARY_OBJECTS,
    price_evidence_trust_boundary_problems,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "002_price_evidence.sql"
TRUST_POLICY_MIGRATION_PATH = ROOT / "migrations" / "003_price_evidence_trust_policy.sql"
TRUST_BOUNDARY_MIGRATION_PATH = ROOT / "migrations" / "004_closed_price_trust_boundaries.sql"
MAX_CANDIDATE_AMOUNT = 1_000_000_000_000.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    def check_keys(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("JSON object keys must be strings")
            for nested in item.values():
                check_keys(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check_keys(nested)

    check_keys(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


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


def _validate_observed_destination(url: str) -> None:
    """Reject malformed and obvious local destinations before retaining fetch claims.

    This is not DNS/redirect validation and does not authorize a fetch. A real fetch
    service must resolve and pin every hop and recheck every redirect destination.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("Observed destination URL is malformed") from None
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Observed destination must be an absolute HTTP(S) URL without credentials")
    if port not in (None, 80, 443):
        raise ValueError("Observed destination uses a prohibited port")
    hostname = host.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise ValueError("Observed destination uses a prohibited local hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Observed destination resolves to a prohibited non-public IP literal")


def price_evidence_schema_installed(conn: sqlite3.Connection) -> bool:
    return not price_evidence_trust_boundary_problems(conn)


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
    } | {name for names in PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS.values() for name in names} \
      | {name for names in PRICE_EVIDENCE_TRUST_BOUNDARY_OBJECTS.values() for name in names}
    remnants = sorted(objects & all_002_names)
    if remnants:
        raise RuntimeError(
            "Migration 002/003/004 is partially installed; the fixture helper will not delete or replace "
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
        for statement in _sql_statements(TRUST_BOUNDARY_MIGRATION_PATH.read_text(encoding="utf-8")):
            conn.execute(statement)
        problems = price_evidence_trust_boundary_problems(conn)
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
        problems = price_evidence_trust_boundary_problems(conn)
        raise RuntimeError(
            "Migrations 002/003/004 (price evidence trust boundary) are missing or incomplete: " + "; ".join(problems)
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
        problems = price_evidence_trust_boundary_problems(conn)
        raise RuntimeError(
            "Migrations 002/003/004 (price evidence trust boundary) are missing or incomplete: "
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
    """Append untrusted caller-supplied bytes and claims for audit only.

    No fetch occurs here. Caller metadata is retained as a claim, and the new
    provenance state is always UNVERIFIED.
    """
    if not price_evidence_schema_installed(conn):
        raise RuntimeError("Apply migrations 002, 003, and 004 before recording landing-page evidence")
    observed_destination = landing_url_from_ad_observation(conn, facebook_ad_observation_id)
    if not observed_destination:
        raise ValueError("The exact Facebook observation has no destination URL")
    if original_url is not None and original_url != observed_destination:
        raise ValueError("Fetch original_url must match the destination on the exact Facebook observation")
    _validate_observed_destination(observed_destination)
    original_url = observed_destination
    if fetch_status not in {"FETCHED", "INACCESSIBLE", "FAILED"}:
        raise ValueError(f"Unsupported fetch status: {fetch_status}")
    if content is not None and not isinstance(content, (str, bytes)):
        raise TypeError("content must be UTF-8 text, bytes, or None")
    if evidence is not None and not isinstance(evidence, dict):
        raise TypeError("fetch evidence metadata must be a dictionary")
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
               fetcher_name, fetcher_version, evidence_json, idempotency_key,
               provenance_status
           ) VALUES (?, ?, NULL, ?, 'FAILED', NULL, NULL, ?,
                     'untrusted_caller_submission', 'untrusted-v1', ?, ?, 'UNVERIFIED')
           ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            facebook_ad_observation_id, original_url, _utc_now(), content_sha256,
            _json({
                "provenance_status": "UNVERIFIED",
                "source_origin_authenticated": False,
                "content_sha256_semantics": "integrity_only_not_origin_authentication",
                "caller_claims": {
                    "fetched_at": fetched_at,
                    "fetch_status": fetch_status,
                    "resolved_url": resolved_url,
                    "http_status": http_status,
                    "content_type": content_type,
                    "fetcher_name": fetcher_name,
                    "fetcher_version": fetcher_version,
                    "metadata": evidence or {},
                },
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
        raise RuntimeError("Apply migrations 002, 003, and 004 before recording price evidence")
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
    fetch_record: Any = {}
    caller_claims: Any = {}
    try:
        fetch_record = json.loads(source["evidence_json"] or "{}")
        caller_claims = fetch_record.get("caller_claims", {}) if isinstance(fetch_record, dict) else {}
        content_record = fetch_record.get("raw_source_content") if isinstance(fetch_record, dict) else None
        if not isinstance(content_record, dict) or content_record.get("encoding") != "base64":
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
        fetch_status=("FETCHED" if content_problem is None else "FAILED"),
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
    provenance_verified = source["provenance_status"] == "VERIFIED"
    if not provenance_verified:
        candidates = [replace(
            candidate,
            currency_code=None,
            currency_basis="UNKNOWN",
            unit_status="UNKNOWN",
            product_identity_state="UNVERIFIED",
            product_key=None,
            quality_status=(
                "UNIT_OR_CURRENCY_UNCONFIRMED"
                if candidate.extraction_status == "SUCCESS"
                and candidate.quality_status == "VALID"
                else candidate.quality_status
            ),
            reason_codes=tuple(dict.fromkeys((*candidate.reason_codes, "FETCH_PROVENANCE_UNVERIFIED"))),
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
                        "content_hash_integrity_verified": raw_content is not None,
                        "content_hash_proves_origin": False,
                        "provenance_status": source["provenance_status"],
                        "source_origin_authenticated": provenance_verified,
                        "content_issue": content_problem,
                        "fetch_status": source["fetch_status"],
                        "original_url": source["original_url"],
                        "resolved_url": source["resolved_url"],
                        "fetch_claims": caller_claims,
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
    idempotency_key: str | None = None,
) -> int:
    """Append an untrusted review proposal; this repository cannot approve prices."""
    if not price_evidence_schema_installed(conn):
        raise RuntimeError("Migrations 002, 003, and 004 are required before recording review proposals")
    if decision == "APPROVED":
        raise ValueError(
            "APPROVED is unavailable: no trusted review service or reviewer identity provider is configured"
        )
    if decision not in {"REJECTED", "NEEDS_MORE_EVIDENCE"}:
        raise ValueError(f"Unsupported review decision: {decision!r}")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("Review proposals require a non-empty reviewer label")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise ValueError("Review proposals require a timestamp string")
    if not isinstance(reason_codes, (tuple, list)) or not all(isinstance(code, str) for code in reason_codes):
        raise TypeError("reason_codes must be a sequence of strings")
    review_reasons = list(reason_codes)
    if evidence is not None and not isinstance(evidence, dict):
        raise TypeError("review evidence must be a dictionary")
    review_evidence = dict(evidence or {})
    if "review_authorization" in review_evidence:
        raise ValueError("review_authorization is reserved for a future trusted review service")
    if currency_code is not None and not isinstance(currency_code, str):
        raise TypeError("currency_code must be a string or None")
    if currency_basis not in (None, "SOURCE_CONFIRMED", "INDEPENDENTLY_VERIFIED", "UNKNOWN"):
        raise ValueError(f"Unsupported currency basis: {currency_basis!r}")
    recognized_code = normalize_currency_code(currency_code)
    if currency_code is not None and recognized_code is None:
        # Preserve raw reviewer input while keeping the structured code unconfirmed.
        review_evidence.setdefault("unrecognized_currency_code_raw", currency_code)
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

    source = conn.execute("SELECT 1 FROM price_evidence WHERE id = ?", (price_evidence_id,)).fetchone()
    if source is None:
        raise ValueError(f"Unknown price evidence id: {price_evidence_id}")

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
        "authorization_status": "UNVERIFIED",
    })
    reasons_json = _json(review_reasons)
    evidence_json = _json(review_evidence)
    conn.execute(
        """INSERT INTO price_evidence_reviews (
               price_evidence_id, decision, quality_status, currency_code,
               currency_basis, unit_status, product_identity_state, product_identity_key,
               unit_interpretation, price_type,
               reviewer, reviewed_at, reason_codes_json, supporting_evidence_json,
               idempotency_key, authorization_status
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNVERIFIED')
           ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            price_evidence_id, decision, quality_status, currency_code,
            currency_basis, unit_status, product_identity_state,
            product_identity_key, unit_interpretation, price_type,
            reviewer, reviewed_at, reasons_json, evidence_json, key,
        ),
    )
    return int(conn.execute(
        "SELECT id FROM price_evidence_reviews WHERE idempotency_key = ?", (key,)
    ).fetchone()[0])

