"""Observe promoted Ouedkniss store listings in Foundation V2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import random
import sqlite3
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import config
from config import connect
from price_evidence.repository import append_ouedkniss_evidence_for_capture
from schema_validation import price_evidence_currency_policy_problems, schema_readiness_message


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [SENSORS] - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

API_URL = "https://api.ouedkniss.com/graphql"
PLATFORM = "Ouedkniss"
UNKNOWN_CURRENCY = "UNKNOWN"
REQUEST_TIMEOUT_SECONDS = 15
PRICE_UNIT_MULTIPLIERS = {
    # Parse known source amount scales without claiming a currency.
    "UNIT": 1,
    "MILLION": 1_000_000,
    "BILLION": 1_000_000_000,
    "UNIT_PER_SQUARE": 1,
    "MILLION_PER_SQUARE": 1_000_000,
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
]

GRAPHQL_QUERY = """
fragment StoreCardFullContent on Store {
    id name slug description imageUrl followerCount announcementsCount iAmFollowing url
    mainLocation { id location { id region { name __typename } city { name __typename } __typename } __typename }
    announcements(count: 8, page: 1, filter: {withImages: true}) {
        data { id title slug price priceUnit priceType status defaultMedia(size: SMALL) { mediaUrl mimeType thumbnail __typename } __typename }
        __typename
    }
    __typename
}
query FrontStoreQuery($location: PromotionLocation!, $count: Int) {
    storeList: promotionFetch(location: $location, maxCount: $count) {
        id target { ... on Store { ...StoreCardFullContent __typename } __typename }
        __typename
    }
}
"""

PAYLOAD = {
    "operationName": "FrontStoreQuery",
    "variables": {"location": "CROSS_HOME_FRONT_STORE", "count": 16},
    "query": GRAPHQL_QUERY,
}


@dataclass
class RunStats:
    sellers_observed: int = 0
    listings_observed: int = 0
    new_listings: int = 0
    existing_listings: int = 0
    observations: int = 0
    duplicate_observations: int = 0
    observations_with_price: int = 0
    observations_with_null_price: int = 0
    errors: int = 0


PriceExtractor = Callable[[Mapping[str, Any]], tuple[Optional[float], Optional[str]]]


class SellerIdentityConflict(Exception):
    """Raised when exact listing evidence points to conflicting seller rows."""


class PriceEvidenceSchemaNotReady(RuntimeError):
    """Raised before capture when the configured database lacks valid trust-policy migrations."""


def require_price_evidence_schema() -> None:
    """Check the configured database read-only before any capture fetch or write."""
    database_path = config.DATABASE_PATH.expanduser().resolve()
    if not database_path.is_file():
        raise PriceEvidenceSchemaNotReady(schema_readiness_message(
            ["configured database file does not exist"], database=str(database_path)
        ))
    conn = config.connect(read_only=True)
    try:
        problems = price_evidence_currency_policy_problems(conn)
        fk_violations = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        if fk_violations:
            problems.append(f"foreign_key_check returned {len(fk_violations)} violation(s)")
        if problems:
            raise PriceEvidenceSchemaNotReady(
                schema_readiness_message(problems, database=str(database_path))
            )
    finally:
        conn.close()


def get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.ouedkniss.com",
        "Referer": "https://www.ouedkniss.com/",
    }


def fetch_data() -> Optional[dict[str, Any]]:
    """Fetch the current promoted-store response without writing to the database."""
    request = Request(
        API_URL,
        data=json.dumps(PAYLOAD).encode("utf-8"),
        headers=get_headers(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            logger.info("Ouedkniss GraphQL response received (HTTP %s).", response.status)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.error("Network failure while fetching Ouedkniss GraphQL data: %s", exc)
        return None

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("Malformed Ouedkniss JSON response: %s", exc)
        return None

    if not isinstance(data, dict):
        logger.error("Malformed Ouedkniss response: top-level JSON value is not an object.")
        return None
    return data


def polite_sleep() -> None:
    time.sleep(random.uniform(1.5, 3.5))


def utc_timestamp(value: Optional[datetime | str] = None) -> str:
    """Return a timezone-aware UTC timestamp in consistent ISO-8601 form."""
    if value is None:
        timestamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    else:
        raise TypeError("observed_at must be a datetime, ISO-8601 string, or None")

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("observed_at must include timezone information")
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def extract_price(announcement: Mapping[str, Any]) -> tuple[Optional[float], None]:
    """Parse a numeric amount candidate without inferring a currency."""
    source_price = announcement.get("price")
    if source_price is None:
        return None, None
    if isinstance(source_price, bool) or not isinstance(source_price, (int, float)):
        raise ValueError("source price is not numeric")

    source_unit = announcement.get("priceUnit")
    multiplier = PRICE_UNIT_MULTIPLIERS.get(source_unit)
    if multiplier is None:
        raise ValueError(f"unsupported or missing source price unit: {source_unit!r}")
    return float(source_price) * multiplier, None


def normalize_listing_status(source_status: Any) -> str:
    """Translate only the verified published source status into Foundation V2."""
    if source_status == "PUBLISHED":
        return "active"
    return "unknown"


def _valid_source_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    result = str(value).strip()
    return result if result else None


def _optional_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value != "" else None


def _wilaya_from_store(store: Mapping[str, Any]) -> Optional[str]:
    main_location = store.get("mainLocation")
    location = main_location.get("location") if isinstance(main_location, Mapping) else None
    region = location.get("region") if isinstance(location, Mapping) else None
    if not isinstance(region, Mapping):
        return None
    return _optional_text(region.get("name"))


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _dump_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _listing_url(
    store_slug: Optional[str], announcement_slug: Optional[str], source_listing_id: str
) -> Optional[str]:
    if not store_slug or not announcement_slug:
        return None
    store_part = quote(store_slug, safe="-._~")
    announcement_part = quote(announcement_slug, safe="-._~")
    listing_part = quote(source_listing_id, safe="-._~")
    return f"https://www.ouedkniss.com/store/{store_part}/annonce/{announcement_part}/{listing_part}"


def _seller_payload(
    source_seller_id: str,
    store_slug: Optional[str],
    display_name: Optional[str],
    wilaya: Optional[str],
    observed_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_seller_id": source_seller_id,
        "last_observed_at": observed_at,
    }
    for key, value in (
        ("store_slug", store_slug),
        ("display_name", display_name),
        ("wilaya", wilaya),
    ):
        if value is not None:
            payload[key] = value
    return payload


def _merge_source_payload(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge current metadata without erasing previously captured optional fields."""
    merged = dict(previous)
    for key, value in current.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_source_payload(merged[key], value)
        elif value is not None:
            merged[key] = value
    return merged


def _get_or_create_seller(
    cursor: sqlite3.Cursor,
    *,
    source_seller_id: str,
    store_slug: Optional[str],
    display_name: Optional[str],
    wilaya: Optional[str],
    observed_at: str,
    listing_identities: list[tuple[str, Optional[str]]],
) -> tuple[int, bool]:
    cursor.execute(
        "SELECT id, display_name, wilaya, source_payload_json "
        "FROM sellers WHERE platform = ? AND source_seller_id = ?",
        (PLATFORM, source_seller_id),
    )
    row = cursor.fetchone()
    payload = _seller_payload(
        source_seller_id, store_slug, display_name, wilaya, observed_at
    )

    if row is None:
        legacy_candidates: dict[int, sqlite3.Row] = {}
        candidate_evidence: dict[int, dict[int, dict[str, Any]]] = {}
        conflicts: list[str] = []

        for incoming_listing_id, incoming_url in listing_identities:
            cursor.execute(
                """SELECT ml.id, ml.source_listing_id, ml.url, ml.seller_id,
                          s.platform AS seller_platform,
                          s.source_seller_id AS seller_source_seller_id
                   FROM marketplace_listings AS ml
                   LEFT JOIN sellers AS s ON s.id = ml.seller_id
                   WHERE ml.platform = ?
                     AND (ml.source_listing_id = ? OR (? IS NOT NULL AND ml.url = ?))""",
                (PLATFORM, incoming_listing_id, incoming_url, incoming_url),
            )
            matched_rows = cursor.fetchall()
            if len(matched_rows) > 1:
                conflicts.append(
                    f"listing ID {incoming_listing_id} and URL match different marketplace rows"
                )

            for matched in matched_rows:
                if matched["source_listing_id"] not in (None, incoming_listing_id):
                    conflicts.append(
                        f"listing URL for {incoming_listing_id} is assigned to source ID "
                        f"{matched['source_listing_id']}"
                    )
                    continue
                if matched["seller_id"] is None:
                    continue
                if matched["seller_platform"] != PLATFORM:
                    conflicts.append(
                        f"listing {matched['id']} points to a seller on a different platform"
                    )
                    continue

                matched_seller_id = int(matched["seller_id"])
                if matched["seller_source_seller_id"] is None:
                    cursor.execute(
                        "SELECT id, display_name, wilaya, first_seen_at, source_payload_json "
                        "FROM sellers WHERE id = ?",
                        (matched_seller_id,),
                    )
                    candidate = cursor.fetchone()
                    if candidate is None:
                        conflicts.append(
                            f"listing {matched['id']} references a missing seller"
                        )
                        continue
                    legacy_candidates[matched_seller_id] = candidate
                    candidate_evidence.setdefault(matched_seller_id, {})[
                        int(matched["id"])
                    ] = {
                        "marketplace_listing_id": int(matched["id"]),
                        "source_listing_id": matched["source_listing_id"]
                        or incoming_listing_id,
                        "url": matched["url"],
                    }
                elif matched["seller_source_seller_id"] != source_seller_id:
                    conflicts.append(
                        f"listing {matched['id']} belongs to stable seller ID "
                        f"{matched['seller_source_seller_id']}"
                    )

        if len(legacy_candidates) > 1:
            conflicts.append(
                "exact listing evidence points to multiple legacy sellers "
                f"{sorted(legacy_candidates)}"
            )
        if conflicts:
            reason = "; ".join(sorted(set(conflicts)))
            logger.error(
                "Seller identity conflict for Ouedkniss seller %s; skipping store: %s.",
                source_seller_id,
                reason,
            )
            raise SellerIdentityConflict(reason)

        if legacy_candidates:
            adopted_id, legacy_row = next(iter(legacy_candidates.items()))
            merged_payload = _merge_source_payload(
                _json_object(legacy_row["source_payload_json"]), payload
            )
            merged_payload["identity_resolution"] = {
                "method": "legacy_listing_evidence",
                "adopted_at": observed_at,
                "evidence": sorted(
                    candidate_evidence[adopted_id].values(),
                    key=lambda item: (item["marketplace_listing_id"], item["source_listing_id"]),
                ),
            }
            cursor.execute(
                """UPDATE sellers
                   SET source_seller_id = ?,
                       display_name = COALESCE(?, display_name),
                       wilaya = COALESCE(?, wilaya),
                       first_seen_at = COALESCE(first_seen_at, ?),
                       last_seen_at = ?, source_payload_json = ?
                   WHERE id = ?""",
                (
                    source_seller_id,
                    display_name,
                    wilaya,
                    observed_at,
                    observed_at,
                    _dump_json(merged_payload),
                    adopted_id,
                ),
            )
            logger.info(
                "Adopted legacy Ouedkniss seller %s for stable seller ID %s using exact listing evidence.",
                adopted_id,
                source_seller_id,
            )
            return adopted_id, False

        cursor.execute(
            """INSERT INTO sellers (
                   platform, source_seller_id, display_name, wilaya,
                   first_seen_at, last_seen_at, source_payload_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                PLATFORM,
                source_seller_id,
                display_name,
                wilaya,
                observed_at,
                observed_at,
                _dump_json(payload),
            ),
        )
        return int(cursor.lastrowid), True

    merged_payload = _json_object(row["source_payload_json"])
    merged_payload.update(payload)
    cursor.execute(
        """UPDATE sellers
           SET display_name = ?, wilaya = ?, last_seen_at = ?, source_payload_json = ?
           WHERE id = ?""",
        (
            display_name if display_name is not None else row["display_name"],
            wilaya if wilaya is not None else row["wilaya"],
            observed_at,
            _dump_json(merged_payload),
            row["id"],
        ),
    )
    return int(row["id"]), False


def _source_payload(
    *,
    source_seller_id: str,
    store_slug: Optional[str],
    display_name: Optional[str],
    wilaya: Optional[str],
    source_listing_id: str,
    announcement_slug: Optional[str],
    raw_title: str,
    source_price: Any,
    price_unit: Any,
    price_type: Any,
    source_status: Any,
    media: Any,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "source": PLATFORM,
        "capture": {
            "operation": "FrontStoreQuery",
            "scope": "CROSS_HOME_FRONT_STORE promotionFetch maxCount=16; up to 8 image-bearing announcements per returned store",
            "observed_at": observed_at,
        },
        "seller": {
            "source_seller_id": source_seller_id,
            "store_slug": store_slug,
            "display_name": display_name,
            "wilaya": wilaya,
        },
        "announcement": {
            "source_listing_id": source_listing_id,
            "slug": announcement_slug,
            "raw_title": raw_title,
            "price": source_price,
            "price_unit": price_unit,
            "price_type": price_type,
            "status": source_status,
            "default_media": media if isinstance(media, Mapping) else None,
        },
    }


def _get_or_create_listing(
    cursor: sqlite3.Cursor,
    *,
    source_listing_id: str,
    seller_id: int,
    raw_title: str,
    url: Optional[str],
    source_payload_json: str,
    observed_at: str,
    status: str,
) -> Optional[tuple[int, bool]]:
    cursor.execute(
        "SELECT id, source_listing_id, url, source_payload_json FROM marketplace_listings "
        "WHERE platform = ? AND source_listing_id = ?",
        (PLATFORM, source_listing_id),
    )
    row = cursor.fetchone()

    if url is not None:
        cursor.execute(
            "SELECT id, source_listing_id, url, source_payload_json FROM marketplace_listings "
            "WHERE platform = ? AND url = ?",
            (PLATFORM, url),
        )
        url_row = cursor.fetchone()
        if url_row is not None and (row is None or url_row["id"] != row["id"]):
            if row is None and url_row["source_listing_id"] is None:
                # Adopt a migrated URL-only row instead of violating the V2 URL index.
                row = url_row
            else:
                logger.error(
                    "Listing identity conflict for Ouedkniss listing %s: URL is already assigned to listing %s.",
                    source_listing_id,
                    url_row["id"],
                )
                return None

    if row is None:
        cursor.execute(
            """INSERT INTO marketplace_listings (
                   platform, source_listing_id, seller_id, raw_title, url, status,
                   first_seen_at, last_seen_at, source_payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                PLATFORM,
                source_listing_id,
                seller_id,
                raw_title,
                url,
                status,
                observed_at,
                observed_at,
                source_payload_json,
            ),
        )
        return int(cursor.lastrowid), False

    if row["source_listing_id"] not in (None, source_listing_id):
        logger.error(
            "Listing identity conflict for Ouedkniss listing %s: URL row %s has source ID %s.",
            source_listing_id,
            row["id"],
            row["source_listing_id"],
        )
        return None

    merged_payload = _merge_source_payload(
        _json_object(row["source_payload_json"]), _json_object(source_payload_json)
    )
    cursor.execute(
        """UPDATE marketplace_listings
           SET source_listing_id = ?, seller_id = ?, raw_title = ?, url = ?, status = ?,
               first_seen_at = COALESCE(first_seen_at, ?), last_seen_at = ?, source_payload_json = ?
           WHERE id = ?""",
        (
            source_listing_id,
            seller_id,
            raw_title,
            url if url is not None else row["url"],
            status,
            observed_at,
            observed_at,
            _dump_json(merged_payload),
            row["id"],
        ),
    )
    return int(row["id"]), True


def _validated_price(
    announcement: Mapping[str, Any],
    extractor: PriceExtractor,
    source_listing_id: str,
    stats: RunStats,
) -> tuple[Optional[float], str, str]:
    try:
        price, currency = extractor(announcement)
    except Exception:
        logger.exception(
            "Price extraction failed for Ouedkniss listing %s; recording NULL.",
            source_listing_id,
        )
        stats.errors += 1
        price, currency = None, None

    if price is not None:
        try:
            numeric_price = float(price)
        except (OverflowError, TypeError, ValueError):
            numeric_price = math.nan
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(numeric_price)
            or numeric_price < 0
        ):
            logger.error(
                "Price extraction returned an invalid value for Ouedkniss listing %s; recording NULL.",
                source_listing_id,
            )
            stats.errors += 1
            price = None
        else:
            price = numeric_price

    if isinstance(currency, str) and currency.strip():
        return price, currency.strip(), "source_response"
    return price, UNKNOWN_CURRENCY, "currency_unconfirmed"


def _insert_observation(
    cursor: sqlite3.Cursor,
    *,
    listing_id: int,
    observed_at: str,
    price: Optional[float],
    currency: str,
    currency_basis: str,
    price_extraction_source: str,
    source_payload: Mapping[str, Any],
    status: str,
) -> bool:
    observation_payload = dict(source_payload)
    observation_payload["observation"] = {
        "price_extraction": price_extraction_source,
        "currency_basis": currency_basis,
    }
    cursor.execute(
        """INSERT INTO marketplace_listing_observations (
               listing_id, observed_at, price, currency, status, availability, source_payload_json
           ) VALUES (?, ?, ?, ?, ?, 'listed', ?)
           ON CONFLICT(listing_id, observed_at) DO NOTHING""",
        (
            listing_id,
            observed_at,
            price,
            currency,
            status,
            _dump_json(observation_payload),
        ),
    )
    return cursor.rowcount == 1


def process_and_store_data(
    data: Mapping[str, Any],
    observed_at: Optional[datetime | str] = None,
    *,
    price_extractor: PriceExtractor = extract_price,
) -> RunStats:
    """Upsert observed source entities and append one immutable observation per listing."""
    require_price_evidence_schema()
    if not isinstance(data, Mapping):
        logger.error("Malformed Ouedkniss response: top-level value is not an object.")
        raise ValueError("Ouedkniss response must be a JSON object")
    if data.get("errors"):
        logger.error("Malformed Ouedkniss response: GraphQL returned errors: %s", data["errors"])
        raise ValueError("Ouedkniss GraphQL response contains errors")

    response_data = data.get("data")
    if not isinstance(response_data, Mapping) or not isinstance(response_data.get("storeList"), list):
        logger.error("Malformed Ouedkniss response: data.storeList is missing or not a list.")
        raise ValueError("Ouedkniss response must contain data.storeList as a list")

    timestamp = utc_timestamp(observed_at)
    stats = RunStats()
    if price_extractor is extract_price:
        logger.info(
            "Using verified FrontStoreQuery price, price unit, price type, and status fields; "
            "currency remains UNKNOWN unless explicitly supplied by the source."
        )

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = connect()
        with conn:
            cursor = conn.cursor()
            observed_seller_ids: set[str] = set()

            for store_node in response_data["storeList"]:
                if not isinstance(store_node, Mapping):
                    logger.error("Malformed Ouedkniss store entry; skipping it.")
                    stats.errors += 1
                    continue
                store = store_node.get("target")
                if not isinstance(store, Mapping):
                    continue

                source_seller_id = _valid_source_id(store.get("id"))
                if source_seller_id is None:
                    logger.error("Missing stable seller ID in Ouedkniss store response; skipping store.")
                    stats.errors += 1
                    continue

                announcement_group = store.get("announcements")
                announcements = (
                    announcement_group.get("data")
                    if isinstance(announcement_group, Mapping)
                    else None
                )
                if not isinstance(announcements, list):
                    logger.error(
                        "Malformed announcements response for Ouedkniss seller %s; skipping store.",
                        source_seller_id,
                    )
                    stats.errors += 1
                    continue

                valid_announcements: list[tuple[Mapping[str, Any], str, str]] = []
                for announcement in announcements:
                    if not isinstance(announcement, Mapping):
                        logger.error(
                            "Malformed announcement for Ouedkniss seller %s; skipping entry.",
                            source_seller_id,
                        )
                        stats.errors += 1
                        continue
                    source_listing_id = _valid_source_id(announcement.get("id"))
                    if source_listing_id is None:
                        logger.error(
                            "Missing stable listing ID for Ouedkniss seller %s; skipping announcement.",
                            source_seller_id,
                        )
                        stats.errors += 1
                        continue
                    raw_title = announcement.get("title")
                    if not isinstance(raw_title, str):
                        logger.error(
                            "Missing raw title for Ouedkniss listing %s; skipping announcement.",
                            source_listing_id,
                        )
                        stats.errors += 1
                        continue
                    valid_announcements.append((announcement, source_listing_id, raw_title))

                if not valid_announcements:
                    continue

                display_name = _optional_text(store.get("name"))
                store_slug = _optional_text(store.get("slug"))
                wilaya = _wilaya_from_store(store)
                listing_identities = [
                    (
                        source_listing_id,
                        _listing_url(
                            store_slug,
                            _optional_text(announcement.get("slug")),
                            source_listing_id,
                        ),
                    )
                    for announcement, source_listing_id, _raw_title in valid_announcements
                ]
                try:
                    seller_id, _is_new_seller = _get_or_create_seller(
                        cursor,
                        source_seller_id=source_seller_id,
                        store_slug=store_slug,
                        display_name=display_name,
                        wilaya=wilaya,
                        observed_at=timestamp,
                        listing_identities=listing_identities,
                    )
                except SellerIdentityConflict:
                    stats.errors += 1
                    continue
                observed_seller_ids.add(source_seller_id)

                for announcement, source_listing_id, raw_title in valid_announcements:
                    announcement_slug = _optional_text(announcement.get("slug"))
                    url = _listing_url(store_slug, announcement_slug, source_listing_id)
                    source_payload = _source_payload(
                        source_seller_id=source_seller_id,
                        store_slug=store_slug,
                        display_name=display_name,
                        wilaya=wilaya,
                        source_listing_id=source_listing_id,
                        announcement_slug=announcement_slug,
                        raw_title=raw_title,
                        source_price=announcement.get("price"),
                        price_unit=announcement.get("priceUnit"),
                        price_type=announcement.get("priceType"),
                        source_status=announcement.get("status"),
                        media=announcement.get("defaultMedia"),
                        observed_at=timestamp,
                    )
                    entity_payload_json = _dump_json(source_payload)
                    listing_status = normalize_listing_status(announcement.get("status"))
                    listing = _get_or_create_listing(
                        cursor,
                        source_listing_id=source_listing_id,
                        seller_id=seller_id,
                        raw_title=raw_title,
                        url=url,
                        source_payload_json=entity_payload_json,
                        observed_at=timestamp,
                        status=listing_status,
                    )
                    if listing is None:
                        stats.errors += 1
                        continue

                    listing_id, was_existing = listing
                    stats.listings_observed += 1
                    if was_existing:
                        stats.existing_listings += 1
                    else:
                        stats.new_listings += 1

                    price, currency, currency_basis = _validated_price(
                        announcement, price_extractor, source_listing_id, stats
                    )
                    # The legacy snapshot table cannot express an unconfirmed currency safely.
                    # Preserve its raw source amount/unit in the payload and leave its monetary
                    # column empty; the additive evidence table retains a parsed candidate.
                    stored_price = price if currency_basis == "source_response" else None
                    if _insert_observation(
                        cursor,
                        listing_id=listing_id,
                        observed_at=timestamp,
                        price=stored_price,
                        currency=currency,
                        currency_basis=currency_basis,
                        price_extraction_source=(
                            "FrontStoreQuery.price"
                            if price_extractor is extract_price
                            else "provided_by_price_extractor"
                        ),
                        source_payload=source_payload,
                        status=listing_status,
                    ):
                        stats.observations += 1
                        if stored_price is None:
                            stats.observations_with_null_price += 1
                        else:
                            stats.observations_with_price += 1
                    else:
                        stats.duplicate_observations += 1
                        logger.warning(
                            "Duplicate Ouedkniss observation ignored for listing %s at %s; existing history was preserved.",
                            source_listing_id,
                            timestamp,
                        )

            stats.sellers_observed = len(observed_seller_ids)
            append_ouedkniss_evidence_for_capture(
                conn, timestamp, extracted_at=timestamp
            )
        logger.info(
            "Ouedkniss capture complete: %d sellers, %d listings, %d new, %d existing, "
            "%d observations, %d with prices, %d with NULL prices, %d errors.",
            stats.sellers_observed,
            stats.listings_observed,
            stats.new_listings,
            stats.existing_listings,
            stats.observations,
            stats.observations_with_price,
            stats.observations_with_null_price,
            stats.errors,
        )
        return stats
    except sqlite3.Error:
        logger.exception("Database failure during Ouedkniss capture; transaction rolled back.")
        raise
    finally:
        if conn is not None:
            conn.close()


def run_sensor() -> Optional[RunStats]:
    logger.info("Starting Ouedkniss FrontStoreQuery sensor.")
    require_price_evidence_schema()
    data = fetch_data()
    if data is None:
        logger.warning("Ouedkniss sensor run aborted because no valid response was received.")
        return None
    polite_sleep()
    try:
        return process_and_store_data(data)
    except (ValueError, sqlite3.Error):
        logger.exception("Ouedkniss sensor run failed.")
        return None


if __name__ == "__main__":
    try:
        run_sensor()
    except PriceEvidenceSchemaNotReady as exc:
        logger.error("%s", exc)
        raise SystemExit(2)
