"""Separate SQLite store for future product-page pilot observations.

The module never opens the application database. Creation requires an explicit,
approved retention policy and a caller-selected database path that is checked
against the protected production database identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from database_safety import (
    PROTECTED_PRODUCTION_DATABASE_PATH,
    resolve_database_path,
    same_database_file,
)

SCHEMA = Path(__file__).with_name("schema.sql")
_SENSITIVE_QUERY_KEY = re.compile(
    r"auth|authorization|token|secret|password|passwd|session|sid|cookie|csrf|"
    r"access.?key|api.?key|email|phone|mobile|customer.?id|user.?id|account.?id|"
    r"national.?id|passport|birth.?date|first.?name|last.?name|address|uid|user|"
    r"visitor|profile.?id|person.?id|fbclid|gclid|msclkid",
    re.IGNORECASE,
)
_PRODUCT_ID_QUERY_KEY = re.compile(r"product.?id|variant.?id|sku|mpn|gtin|model", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"(?:token|session|password|authorization|api[_-]?key)=",
    re.IGNORECASE,
)
_PRIVATE_PROPERTY = re.compile(
    r"(^|[_-])(html|body|cookie|authorization|auth|session|token|secret|password|"
    r"email|phone|customer.?id|user.?id|account.?id|uid|visitor|profile.?id|person.?id|"
    r"birth.?date|national.?id|first.?name|last.?name|trusted.?price|price.?approved)([_-]|$)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_VALUE = re.compile(r"(?<!\w)(?:\+|00)?\d(?:[\d .()\-]{6,}\d)(?!\w)")
_PHONE_SAFE_PROPERTY = re.compile(
    r"(^|_)(source_ad_(record|observation|library)_id|candidate_url_id|merchant_id|"
    r"page_source_id|source_page_id|record_id|observation_id|page_id|library_id|"
    r"sku|mpn|gtin|variant_id)$",
    re.IGNORECASE,
)
_ERROR_CODE = re.compile(r"[A-Z0-9_:-]{1,100}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StoreSafetyError(RuntimeError):
    """The requested research-store operation violates its isolation policy."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string with an explicit UTC offset")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ValueError("timestamp must be an ISO-8601 string with an explicit UTC offset") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be expressed in UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_url(value: str | None, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("URL must be a bounded absolute HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("URL is malformed") from None
    if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or port not in (None, 443) or parsed.fragment
            or any(character in parsed.netloc for character in "\r\n")):
        raise ValueError("URL must be HTTPS on the default port, without credentials or fragments")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    retained = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if (_SENSITIVE_QUERY_KEY.search(key) or _SENSITIVE_VALUE.search(item) or _EMAIL.search(item)
                or (_PHONE_VALUE.search(item) and not _PRODUCT_ID_QUERY_KEY.search(key))):
            continue
        retained.append((key, item))
    return urlunsplit(("https", host, parsed.path or "/", urlencode(retained, doseq=True), ""))


def _safe_tree(value: Any, *, depth: int = 0, phone_safe: bool = False) -> None:
    if depth > 8:
        raise ValueError("stored JSON exceeds the supported nesting depth")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers are not supported")
        return
    if isinstance(value, str):
        if (len(value) > 4096 or _EMAIL.search(value) or _SENSITIVE_VALUE.search(value)
                or (_PHONE_VALUE.search(value) and not phone_safe)):
            raise ValueError("stored text is too long or contains a sensitive value")
        if re.search(r"<\s*/?\s*[a-z][^>]*>", value, re.IGNORECASE):
            raise ValueError("raw HTML is not accepted by the research store")
        return
    if isinstance(value, list) or isinstance(value, tuple):
        if len(value) > 1000:
            raise ValueError("stored JSON list exceeds the supported size")
        for item in value:
            _safe_tree(item, depth=depth + 1, phone_safe=phone_safe)
        return
    if isinstance(value, dict):
        if len(value) > 200:
            raise ValueError("stored JSON object exceeds the supported size")
        for key, item in value.items():
            if isinstance(key, str) and re.search(r"(^|[_-])(html|body)([_-]|$)", key, re.IGNORECASE):
                raise ValueError("raw HTML is not accepted by the research store")
            if not isinstance(key, str) or len(key) > 120 or _PRIVATE_PROPERTY.search(key):
                raise ValueError("stored JSON contains a private or out-of-scope property")
            _safe_tree(item, depth=depth + 1, phone_safe=bool(_PHONE_SAFE_PROPERTY.search(key)))
        return
    raise ValueError("stored values must be JSON-safe")


@dataclass(frozen=True)
class RetentionPolicy:
    """An operator-approved retention choice; the implementation has no default."""

    policy_id: str
    retention_days: int
    approved_by: str
    approval_reference: str
    approved_at_utc: str

    def __post_init__(self) -> None:
        for name in ("policy_id", "approved_by", "approval_reference"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must be a bounded non-empty value")
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int) or self.retention_days < 1:
            raise ValueError("retention_days must be an explicitly approved positive integer")
        object.__setattr__(self, "approved_at_utc", _utc(self.approved_at_utc))


@dataclass(frozen=True)
class CandidateUrl:
    candidate_url_id: str
    merchant_id: str
    merchant_display_name: str
    operator_identity_confidence: str
    candidate_url: str
    url_classification: str
    eligibility_status: str
    eligibility_reason: str
    created_at_utc: str
    discovery_provenance: Mapping[str, Any]
    legal_operator_identity: str | None = None
    source_ad_record_id: int | None = None
    source_ad_observation_id: int | None = None
    source_ad_library_id: str | None = None
    source_ad_url: str | None = None


@dataclass(frozen=True)
class ObservationInput:
    candidate_url_id: str
    merchant_id: str
    idempotency_key: str
    observed_at_utc: str
    requested_url: str
    fetch_outcome: str
    extraction_outcome: str
    raw_observed_values: Mapping[str, Any]
    normalized_interpretations: Mapping[str, Any]
    redirect_history: tuple[Mapping[str, Any], ...] = ()
    final_url: str | None = None
    http_status: int | None = None
    fetch_error_code: str | None = None
    content_sha256: str | None = None
    fetcher_version: str | None = None
    extraction_version: str | None = None


class ResearchObservationStore:
    """Small isolated SQLite repository; observations cannot be edited or deleted."""

    def __init__(self, path: Path, retention_policy: RetentionPolicy, connection: sqlite3.Connection):
        self.path = path
        self.retention_policy = retention_policy
        self._connection = connection

    @classmethod
    def create(cls, path: str | Path, *, retention_policy: RetentionPolicy) -> "ResearchObservationStore":
        resolved = cls._checked_path(path)
        if resolved.exists():
            raise FileExistsError(f"research store already exists: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(resolved)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.executescript(SCHEMA.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO retention_policies(policy_id,retention_days,approved_by,approval_reference,approved_at_utc) VALUES(?,?,?,?,?)",
                (retention_policy.policy_id, retention_policy.retention_days, retention_policy.approved_by,
                 retention_policy.approval_reference, retention_policy.approved_at_utc),
            )
            connection.commit()
        except Exception:
            connection.close()
            try:
                resolved.unlink()
            except OSError:
                pass
            raise
        return cls(resolved, retention_policy, connection)

    @classmethod
    def open(cls, path: str | Path, *, retention_policy: RetentionPolicy) -> "ResearchObservationStore":
        resolved = cls._checked_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"research store does not exist: {resolved}")
        connection = sqlite3.connect(resolved.as_uri() + "?mode=rw", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        row = connection.execute("SELECT * FROM retention_policies WHERE policy_id=?", (retention_policy.policy_id,)).fetchone()
        expected = (retention_policy.retention_days, retention_policy.approved_by,
                    retention_policy.approval_reference, retention_policy.approved_at_utc)
        actual = None if row is None else (row["retention_days"], row["approved_by"], row["approval_reference"], row["approved_at_utc"])
        if actual != expected:
            connection.close()
            raise StoreSafetyError("the supplied approved retention policy does not match the isolated store")
        return cls(resolved, retention_policy, connection)

    @staticmethod
    def _checked_path(path: str | Path) -> Path:
        if not isinstance(path, (str, Path)):
            raise TypeError("an explicit research database path is required")
        resolved = resolve_database_path(path)
        if same_database_file(resolved, PROTECTED_PRODUCTION_DATABASE_PATH):
            raise StoreSafetyError("research observation store cannot target the production Database.sqlite")
        return resolved

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ResearchObservationStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def add_candidate(self, item: CandidateUrl) -> None:
        if not isinstance(item, CandidateUrl):
            raise TypeError("item must be CandidateUrl")
        if not item.merchant_id.strip() or not item.candidate_url_id.strip():
            raise ValueError("merchant and candidate URL IDs are required")
        if item.operator_identity_confidence not in {"UNKNOWN", "LOW", "MEDIUM", "HIGH"}:
            raise ValueError("unsupported operator identity confidence")
        if item.eligibility_status not in {
            "ELIGIBLE_FOR_FINAL_REVIEW", "REVIEW_REQUIRED", "EXCLUDED", "INSUFFICIENT_EVIDENCE"
        }:
            raise ValueError("unsupported candidate eligibility status")
        url = _safe_url(item.candidate_url)
        source_ad_url = _safe_url(item.source_ad_url, nullable=True)
        created = _utc(item.created_at_utc)
        provenance = dict(item.discovery_provenance)
        _safe_tree({
            "merchant_id": item.merchant_id,
            "candidate_url_id": item.candidate_url_id,
            "display_name": item.merchant_display_name,
            "legal_operator_identity": item.legal_operator_identity,
            "eligibility_reason": item.eligibility_reason,
            "source_ad_library_id": item.source_ad_library_id,
        })
        _safe_tree(provenance)
        if item.source_ad_record_id is not None and (
            isinstance(item.source_ad_record_id, bool) or not isinstance(item.source_ad_record_id, int)
            or item.source_ad_record_id < 1
        ):
            raise ValueError("source ad record ID must be a positive integer")
        if item.source_ad_observation_id is not None and (
            isinstance(item.source_ad_observation_id, bool) or not isinstance(item.source_ad_observation_id, int)
            or item.source_ad_observation_id < 1
        ):
            raise ValueError("source ad observation ID must be a positive integer")
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO merchants VALUES(?,?,?,?,?)",
                (item.merchant_id, item.merchant_display_name, item.legal_operator_identity,
                 item.operator_identity_confidence, created),
            )
            merchant = self._connection.execute("SELECT * FROM merchants WHERE merchant_id=?", (item.merchant_id,)).fetchone()
            expected_merchant = (item.merchant_display_name, item.legal_operator_identity,
                                 item.operator_identity_confidence, created)
            if tuple(merchant[key] for key in ("display_name", "legal_operator_identity", "operator_identity_confidence", "created_at_utc")) != expected_merchant:
                raise ValueError("merchant ID already names different identity inputs")
            self._connection.execute(
                """INSERT OR IGNORE INTO candidate_urls(
                    candidate_url_id,merchant_id,candidate_url,url_classification,eligibility_status,
                    eligibility_reason,source_ad_record_id,source_ad_observation_id,source_ad_library_id,
                    source_ad_url,discovery_provenance_json,created_at_utc)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item.candidate_url_id, item.merchant_id, url, item.url_classification,
                 item.eligibility_status, item.eligibility_reason, item.source_ad_record_id,
                 item.source_ad_observation_id, item.source_ad_library_id, source_ad_url,
                 _json(provenance), created),
            )
            candidate = self._connection.execute(
                "SELECT * FROM candidate_urls WHERE candidate_url_id=?", (item.candidate_url_id,)
            ).fetchone()
            expected = (item.merchant_id, url, item.url_classification, item.eligibility_status,
                        item.eligibility_reason, item.source_ad_record_id, item.source_ad_observation_id,
                        item.source_ad_library_id, source_ad_url, _json(provenance), created)
            actual = tuple(candidate[key] for key in (
                "merchant_id", "candidate_url", "url_classification", "eligibility_status",
                "eligibility_reason", "source_ad_record_id", "source_ad_observation_id",
                "source_ad_library_id", "source_ad_url", "discovery_provenance_json", "created_at_utc",
            ))
            if actual != expected:
                raise ValueError("candidate URL ID already names different candidate inputs")

    def append_observation(self, item: ObservationInput) -> str:
        if not isinstance(item, ObservationInput):
            raise TypeError("item must be ObservationInput")
        if not item.idempotency_key or len(item.idempotency_key) > 240:
            raise ValueError("a bounded stable idempotency key is required")
        observed = _utc(item.observed_at_utc)
        requested = _safe_url(item.requested_url)
        final = _safe_url(item.final_url, nullable=True)
        if (not isinstance(item.fetch_outcome, str) or item.fetch_outcome != item.fetch_outcome.strip()
                or not item.fetch_outcome or len(item.fetch_outcome) > 100):
            raise ValueError("fetch outcome must be a bounded non-empty status")
        if (not isinstance(item.extraction_outcome, str) or item.extraction_outcome != item.extraction_outcome.strip()
                or not item.extraction_outcome or len(item.extraction_outcome) > 100):
            raise ValueError("extraction outcome must be a bounded non-empty status")
        if item.http_status is not None and (isinstance(item.http_status, bool) or not 100 <= item.http_status <= 599):
            raise ValueError("HTTP status must be in range 100..599")
        if item.fetch_error_code is not None and not _ERROR_CODE.fullmatch(item.fetch_error_code):
            raise ValueError("fetch error must be a bounded uppercase error code")
        if item.content_sha256 is not None and not _SHA256.fullmatch(item.content_sha256):
            raise ValueError("content hash must be lowercase SHA-256 hex")
        if item.fetcher_version is not None and len(item.fetcher_version) > 120:
            raise ValueError("fetcher version is too long")
        if item.extraction_version is not None and len(item.extraction_version) > 120:
            raise ValueError("extraction version is too long")

        redirects = []
        previous = requested
        if len(item.redirect_history) > 3:
            raise ValueError("redirect history exceeds the collector limit")
        for hop in item.redirect_history:
            if not isinstance(hop, Mapping):
                raise ValueError("redirect history entries must be objects")
            from_url = _safe_url(hop.get("from_url"))
            to_url = _safe_url(hop.get("to_url"))
            status = hop.get("status_code", hop.get("status"))
            if from_url != previous or status not in {301, 302, 303, 307, 308}:
                raise ValueError("redirect history must be contiguous and contain validated redirect statuses")
            clean_hop = {"from_url": from_url, "status": status, "to_url": to_url}
            ref = hop.get("destination_review_reference", hop.get("to_eligibility_reference"))
            if ref is not None:
                if not isinstance(ref, str) or len(ref) > 200 or _EMAIL.search(ref):
                    raise ValueError("redirect review reference must be a bounded non-personal identifier")
                clean_hop["destination_review_reference"] = ref
            redirects.append(clean_hop)
            previous = to_url
        if final is not None and redirects and final != previous:
            raise ValueError("final URL does not match the validated redirect history")
        if final is not None and not redirects and final != requested:
            raise ValueError("final URL without redirects must match the requested URL")
        if item.fetch_outcome == "FETCHED" and (final is None or item.http_status is None or not 200 <= item.http_status < 300):
            raise ValueError("a fetched outcome requires a final URL and 2xx HTTP status")

        raw_values = dict(item.raw_observed_values)
        interpretations = dict(item.normalized_interpretations)
        _safe_tree(raw_values)
        _safe_tree(interpretations)
        if len(_json(raw_values).encode("utf-8")) > 256_000 or len(_json(interpretations).encode("utf-8")) > 256_000:
            raise ValueError("observation values exceed the bounded storage size")

        candidate = self._connection.execute(
            "SELECT * FROM candidate_urls WHERE candidate_url_id=?", (item.candidate_url_id,)
        ).fetchone()
        if candidate is None or candidate["merchant_id"] != item.merchant_id:
            raise ValueError("observation must reference a known candidate URL and its merchant")
        if requested != candidate["candidate_url"]:
            raise ValueError("requested URL must match the registered candidate URL after safe redaction")
        observed_dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        retention_due = (observed_dt + timedelta(days=self.retention_policy.retention_days)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        observation_id = str(uuid5(NAMESPACE_URL, "kratzwerk-research-observation:" + item.idempotency_key))
        discovery = candidate["discovery_provenance_json"]
        values = (
            observation_id, item.idempotency_key, item.merchant_id, item.candidate_url_id,
            candidate["source_ad_record_id"], candidate["source_ad_observation_id"],
            candidate["source_ad_library_id"], candidate["source_ad_url"], discovery,
            observed, self.retention_policy.policy_id, retention_due, requested,
            _json(redirects), final, item.http_status, item.fetch_outcome,
            item.fetch_error_code, item.extraction_outcome, _json(raw_values),
            _json(interpretations), item.content_sha256, item.fetcher_version,
            item.extraction_version,
        )
        existing = self._connection.execute(
            "SELECT * FROM observations WHERE idempotency_key=?", (item.idempotency_key,)
        ).fetchone()
        if existing is not None:
            actual = tuple(existing[key] for key in (
                "observation_id", "idempotency_key", "merchant_id", "candidate_url_id",
                "source_ad_record_id", "source_ad_observation_id", "source_ad_library_id",
                "source_ad_url", "discovery_provenance_json", "observed_at_utc",
                "retention_policy_id", "retention_due_at_utc", "requested_url",
                "redirect_history_json", "final_url", "http_status", "fetch_outcome",
                "fetch_error_code", "extraction_outcome", "raw_observed_values_json",
                "normalized_interpretations_json", "content_sha256", "fetcher_version",
                "extraction_version",
            ))
            if actual != values:
                raise ValueError("idempotency key already names different observation inputs")
            return observation_id
        with self._connection:
            self._connection.execute(
                """INSERT INTO observations VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return observation_id

    def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM observations WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key, column in (
            ("redirect_history", "redirect_history_json"),
            ("discovery_provenance", "discovery_provenance_json"),
            ("raw_observed_values", "raw_observed_values_json"),
            ("normalized_interpretations", "normalized_interpretations_json"),
        ):
            result[key] = json.loads(result.pop(column))
        return result


def observation_from_collection_result(
    result: Any,
    *,
    candidate_url_id: str,
    idempotency_key: str,
    normalized_interpretations: Mapping[str, Any] | None = None,
) -> ObservationInput:
    """Map the existing StoreProductCollector's metadata-only result to storage.

    The collector already discards response bytes. This adapter deliberately
    stores only extracted summaries and field provenance, never the HTML body.
    Normalized interpretations remain empty unless a future reviewed normalizer
    supplies them explicitly.
    """
    fetch = result.fetch_result
    metadata = fetch.metadata
    provenance = result.provenance.to_dict()
    merchant_id = provenance.get("merchant_id")
    if not isinstance(merchant_id, str) or not merchant_id.strip():
        raise ValueError("collector provenance must identify the merchant")
    raw_values = {
        "products": list(result.products),
        "field_provenance": provenance.get("fields", []),
    }
    error = result.error_code or metadata.stop_reason
    if error is not None:
        error = re.sub(r"[^A-Z0-9_:-]", "_", str(error).upper())[:100] or None
    return ObservationInput(
        candidate_url_id=candidate_url_id,
        merchant_id=merchant_id,
        idempotency_key=idempotency_key,
        observed_at_utc=metadata.retrieved_at_utc,
        requested_url=metadata.requested_url,
        redirect_history=tuple(metadata.redirect_hops),
        final_url=metadata.final_url,
        http_status=metadata.status_code,
        fetch_outcome=metadata.outcome,
        fetch_error_code=error,
        extraction_outcome=result.extraction_status,
        raw_observed_values=raw_values,
        normalized_interpretations=dict(normalized_interpretations or {}),
        content_sha256=metadata.sha256,
        fetcher_version=metadata.fetcher_version,
        extraction_version=getattr(result, "extraction_version", None) or "store-product-static-v1",
    )
