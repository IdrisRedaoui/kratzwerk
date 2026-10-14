"""Read-only structural checks shared by migrations and capture entry points.

Schema comparison accepts SQLite's ASCII identifier case equivalence and treats
ordinary non-keyword identifiers as equivalent when quoted with double quotes,
backticks, brackets, or left unquoted. String literals and quoted keywords keep
their meaning. This tokenizer is not a full SQLite parser: Unicode case-folding,
quoted-keyword role inference, extensions, and arbitrary SQLite rewrite
equivalence are outside the accepted dialect. Such definitions are reported as
schema drift before baseline or migration changes.
"""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import Iterable


FOUNDATION_TABLE_COLUMNS: dict[str, set[str]] = {
    "products": {"id", "canonical_name", "category", "attributes_json", "created_at", "updated_at"},
    "sellers": {"id", "platform", "source_seller_id", "display_name", "phone_number", "wilaya", "first_seen_at", "last_seen_at", "source_payload_json"},
    "marketplace_listings": {"id", "platform", "source_listing_id", "seller_id", "product_id", "raw_title", "url", "status", "first_seen_at", "last_seen_at", "source_payload_json"},
    "marketplace_listing_observations": {"id", "listing_id", "observed_at", "price", "currency", "status", "availability", "source_payload_json"},
    "facebook_ads": {"id", "library_id", "url", "page_source_id", "page_name", "platform", "platforms_json", "creative_text", "creative_fingerprint", "first_seen_at", "last_seen_at", "start_date", "status", "product_id", "source_payload_json"},
    "facebook_ad_observations": {"id", "ad_id", "observed_at", "active", "detected_metadata_json", "creative_fingerprint"},
    "product_aliases": {"id", "product_id", "source_type", "raw_alias", "normalized_alias", "source", "created_at"},
    "product_mappings": {"id", "source_type", "source_id", "product_id", "alias_id", "confidence", "mapping_source", "created_at"},
}

FOUNDATION_LEGACY_TABLES = {
    "legacy_products",
    "legacy_sellers",
    "legacy_marketplace_listings",
    "legacy_price_history",
    "legacy_advertising_campaigns",
}

FOUNDATION_REQUIRED_OBJECTS: dict[str, set[str]] = {
    "index": {
        "ux_marketplace_listing_url",
        "ux_facebook_ads_url",
        "ix_marketplace_listings_seller",
        "ix_marketplace_listings_product",
        "ix_marketplace_listings_status",
        "ix_listing_observations_time",
        "ix_facebook_ads_product",
        "ix_facebook_ads_status",
        "ix_ad_observations_time",
        "ix_product_mappings_product",
        "ix_product_alias_lookup",
    },
    "trigger": {
        "trg_listing_observations_no_update",
        "trg_listing_observations_no_delete",
        "trg_ad_observations_no_update",
        "trg_ad_observations_no_delete",
    },
}

PRICE_EVIDENCE_TABLE_COLUMNS: dict[str, set[str]] = {
    "facebook_landing_page_fetches": {
        "id", "facebook_ad_observation_id", "original_url", "resolved_url", "fetched_at",
        "fetch_status", "http_status", "content_type", "content_sha256", "fetcher_name",
        "fetcher_version", "evidence_json", "idempotency_key",
    },
    "price_evidence": {
        "id", "source_platform", "source_entity_key", "marketplace_observation_id",
        "facebook_landing_page_fetch_id", "source_observed_at", "price_observed_at",
        "original_url", "resolved_url", "raw_value_json", "raw_price_text", "candidate_amount",
        "candidate_amount_max", "source_unit", "unit_status", "currency_code", "currency_basis",
        "price_type", "product_identity_state", "product_identity_key", "extraction_method",
        "extraction_version", "extracted_at", "extraction_status", "quality_status",
        "reason_codes_json", "supporting_evidence_json", "evidence_index", "idempotency_key",
    },
    "price_evidence_reviews": {
        "id", "price_evidence_id", "decision", "quality_status", "currency_code",
        "currency_basis", "unit_status", "product_identity_state", "product_identity_key",
        "unit_interpretation", "price_type", "reviewer", "reviewed_at", "reason_codes_json",
        "supporting_evidence_json", "idempotency_key",
    },
}

PRICE_EVIDENCE_REQUIRED_OBJECTS: dict[str, set[str]] = {
    "table": set(PRICE_EVIDENCE_TABLE_COLUMNS),
    "view": {"trusted_price_evidence"},
    "index": {
        "ix_landing_page_fetch_observation_time",
        "ix_price_evidence_marketplace_observation",
        "ix_price_evidence_landing_fetch",
        "ix_price_evidence_source_time",
        "ix_price_evidence_reviews_latest",
    },
    "trigger": {
        "trg_price_evidence_platform_guard",
        "trg_landing_fetch_no_update",
        "trg_landing_fetch_no_delete",
        "trg_price_evidence_no_update",
        "trg_price_evidence_no_delete",
        "trg_price_evidence_reviews_no_update",
        "trg_price_evidence_reviews_no_delete",
    },
}

PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS: dict[str, set[str]] = {
    "view": {"legacy_currency_unconfirmed_observations"},
    "trigger": {
        "trg_price_evidence_currency_allowlist",
        "trg_price_evidence_reviews_currency_allowlist",
        "trg_price_evidence_amount_policy",
        "trg_price_evidence_reviews_authorization",
    },
}


def object_types(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        _sqlite_ascii_lower(str(row[0])): str(row[1])
        for row in conn.execute(
            "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # Table names are supplied only by the constants above, never user input.
    return {
        _sqlite_ascii_lower(str(row[1]))
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    }


_EXPECTED_PRICE_SQL: dict[str, str] | None = None
_EXPECTED_PRICE_CURRENCY_POLICY_SQL: dict[str, str] | None = None
_EXPECTED_FOUNDATION_SQL: dict[str, tuple[str, str]] | None = None
_EXPECTED_FOUNDATION_TABLE_SHAPES: dict[str, tuple] | None = None
_ASCII_LOWER_TABLE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_SQLITE_KEYWORDS = frozenset("""
ABORT ACTION ADD AFTER ALL ALTER ALWAYS ANALYZE AND AS ASC ATTACH AUTOINCREMENT BEFORE BEGIN BETWEEN BY CASCADE CASE CAST CHECK COLLATE COLUMN COMMIT CONFLICT CONSTRAINT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP DATABASE DEFAULT DEFERRABLE DEFERRED DELETE DESC DETACH DISTINCT DO DROP EACH ELSE END ESCAPE EXCEPT EXCLUDE EXCLUSIVE EXISTS EXPLAIN FAIL FALSE FILTER FIRST FOLLOWING FOR FOREIGN FROM FULL GENERATED GLOB GROUP GROUPS HAVING IF IGNORE IMMEDIATE IN INDEX INDEXED INITIALLY INNER INSERT INSTEAD INTERSECT INTO IS ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH MATERIALIZED NATURAL NO NOCASE NOT NOTHING NOTNULL NULL NULLS OF OFFSET ON OR ORDER OTHERS OUTER OVER PARTITION PLAN PRAGMA PRECEDING PRIMARY QUERY RAISE RANGE RECURSIVE REFERENCES REGEXP REINDEX RELEASE RENAME REPLACE RESTRICT RETURNING RIGHT ROLLBACK ROW ROWS SAVEPOINT SELECT SET STORED STRICT TABLE TEMP TEMPORARY THEN TIES TO TRANSACTION TRIGGER TRUE UNBOUNDED UNION UNIQUE UPDATE USING VACUUM VALUES VIEW VIRTUAL WHEN WHERE WINDOW WITH WITHOUT
""".split())


def _sqlite_ascii_lower(value: str) -> str:
    """SQLite folds ASCII identifier letters, but does not Unicode-casefold names."""
    return value.translate(_ASCII_LOWER_TABLE)


def _sql_tokens(sql: str | None) -> tuple[tuple[str, str], ...]:
    """Tokenize SQL and normalize SQLite ASCII identifier equivalence.

    Ordinary identifiers compare the same when quoted with SQLite's accepted
    identifier delimiters or written unquoted. Quoted keywords remain distinct;
    this intentionally avoids treating keyword-looking column names as grammar.
    """
    source = sql or ""
    tokens: list[tuple[str, str]] = []
    i = 0
    operators = ("->>", "<=", ">=", "!=", "<>", "==", "||", "->", "<<", ">>")
    while i < len(source):
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if source.startswith("--", i):
            newline = source.find("\n", i + 2)
            i = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            continue
        if char == "'":
            start = i
            i += 1
            while i < len(source):
                if source[i] == "'":
                    if i + 1 < len(source) and source[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            tokens.append(("literal", source[start:i]))
            continue
        if char in ('"', '`', '['):
            start = i
            close = "]" if char == "[" else char
            i += 1
            while i < len(source):
                if source[i] == close:
                    if close != "]" and i + 1 < len(source) and source[i + 1] == close:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            content = source[start + 1:i - 1] if i <= len(source) and source[i - 1:i] == close else source[start + 1:i]
            if close != "]":
                content = content.replace(close + close, close)
            tokens.append(("identifier", _sqlite_ascii_lower(content)))
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < len(source) and (source[i].isalnum() or source[i] in "_$"):
                i += 1
            word = _sqlite_ascii_lower(source[start:i])
            tokens.append(("keyword" if word.upper() in _SQLITE_KEYWORDS else "identifier", word))
            continue
        if char.isdigit():
            match = re.match(r"(?:0[xX][0-9A-Fa-f]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", source[i:])
            if match:
                value = match.group(0)
                tokens.append(("number", _sqlite_ascii_lower(value)))
                i += len(value)
                continue
        operator = next((op for op in operators if source.startswith(op, i)), None)
        if operator:
            tokens.append(("punctuation", operator))
            i += len(operator)
            continue
        tokens.append(("punctuation", char))
        i += 1
    return tuple(tokens)


def _canonical_sql(sql: str | None) -> str:
    """Canonicalize insignificant SQL formatting without changing quoted meaning."""
    return repr(_sql_tokens(sql))


def _foundation_table_shape(conn: sqlite3.Connection, table: str) -> tuple:
    """Capture declared columns, foreign keys, and explicit/automatic index layouts."""
    columns = tuple(
        (row[0], _sqlite_ascii_lower(str(row[1])), *tuple(row[2:]))
        for row in conn.execute(f'PRAGMA table_xinfo("{table}")')
    )
    foreign_keys = tuple(sorted(
        (row[0], row[1], _sqlite_ascii_lower(str(row[2])),
         _sqlite_ascii_lower(str(row[3])), _sqlite_ascii_lower(str(row[4])), *tuple(row[5:]))
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
    ))
    indexes = []
    for row in conn.execute(f'PRAGMA index_list("{table}")'):
        name = _sqlite_ascii_lower(str(row[1]))
        index_columns = tuple(
            (index_row[0], index_row[1],
             None if index_row[2] is None else _sqlite_ascii_lower(str(index_row[2])),
             index_row[3],
             None if index_row[4] is None else _sqlite_ascii_lower(str(index_row[4])),
             *tuple(index_row[5:]))
            for index_row in conn.execute(f'PRAGMA index_xinfo("{name}")')
        )
        indexes.append((name, int(row[2]), str(row[3]), int(row[4]), index_columns))
    return columns, foreign_keys, tuple(sorted(indexes))


def _expected_foundation_schema() -> tuple[
    dict[str, tuple[str, str]], dict[str, tuple]
]:
    """Run Migration 001 in memory and snapshot every migration-defined definition."""
    global _EXPECTED_FOUNDATION_SQL, _EXPECTED_FOUNDATION_TABLE_SHAPES
    if _EXPECTED_FOUNDATION_SQL is not None and _EXPECTED_FOUNDATION_TABLE_SHAPES is not None:
        return _EXPECTED_FOUNDATION_SQL, _EXPECTED_FOUNDATION_TABLE_SHAPES

    reference = sqlite3.connect(":memory:")
    try:
        # Migration 001 renames and reads these retained legacy tables. Their original
        # definitions are application-specific, so only the columns it reads are needed
        # in the isolated reference database.
        reference.executescript("""
            CREATE TABLE Products (id INTEGER PRIMARY KEY);
            CREATE TABLE Sellers (
                id INTEGER, origin_platform TEXT, seller_name TEXT,
                phone_number TEXT, wilaya TEXT
            );
            CREATE TABLE Marketplace_Listings (
                id INTEGER, platform TEXT, seller_id INTEGER, product_id INTEGER,
                raw_title TEXT, listing_url TEXT, status TEXT, last_seen TEXT,
                current_price REAL
            );
            CREATE TABLE Price_History (id INTEGER PRIMARY KEY);
            CREATE TABLE Advertising_Campaigns (
                id INTEGER, ad_url TEXT, platform TEXT, start_date TEXT,
                status TEXT, product_id INTEGER, targeted_wilayas TEXT
            );
        """)
        migration_path = Path(__file__).resolve().parent / "migrations" / "001_foundation_v2.sql"
        reference.executescript(migration_path.read_text(encoding="utf-8"))

        table_names = set(FOUNDATION_TABLE_COLUMNS)
        explicit_names = {
            name for names in FOUNDATION_REQUIRED_OBJECTS.values() for name in names
        }
        names = table_names | explicit_names
        expected_sql = {
            _sqlite_ascii_lower(str(row[0])): (str(row[1]), str(row[2]))
            for row in reference.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE name IN (%s)"
                % ",".join("?" * len(names)),
                tuple(sorted(names)),
            )
            if row[2] is not None
        }
        table_shapes = {
            table: _foundation_table_shape(reference, table)
            for table in sorted(table_names)
        }
        _EXPECTED_FOUNDATION_SQL = expected_sql
        _EXPECTED_FOUNDATION_TABLE_SHAPES = table_shapes
        return expected_sql, table_shapes
    finally:
        reference.close()


def _expected_price_evidence_sql() -> dict[str, str]:
    global _EXPECTED_PRICE_SQL
    if _EXPECTED_PRICE_SQL is not None:
        return _EXPECTED_PRICE_SQL
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript("""
            CREATE TABLE marketplace_listings(id INTEGER PRIMARY KEY, platform TEXT NOT NULL);
            CREATE TABLE marketplace_listing_observations(
                id INTEGER PRIMARY KEY,
                listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id),
                observed_at TEXT NOT NULL, price REAL, currency TEXT,
                source_payload_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE facebook_ad_observations(
                id INTEGER PRIMARY KEY, ad_id INTEGER, observed_at TEXT
            );
        """)
        sql_path = Path(__file__).resolve().parent / "migrations" / "002_price_evidence.sql"
        script = sql_path.read_text(encoding="utf-8")
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if not sqlite3.complete_statement(pending):
                continue
            statement = pending.strip()
            meaningful = any(
                item.strip() and not item.lstrip().startswith("--")
                for item in statement.splitlines()
            )
            if meaningful:
                reference.execute(statement)
            pending = ""
        if pending.strip():
            raise RuntimeError("Migration 002 SQL has an incomplete statement")
        names = {name for group in PRICE_EVIDENCE_REQUIRED_OBJECTS.values() for name in group}
        _EXPECTED_PRICE_SQL = {
            str(row[0]): str(row[1])
            for row in reference.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN (%s)" % ",".join("?" * len(names)),
                tuple(sorted(names)),
            )
            if row[1] is not None
        }
        return _EXPECTED_PRICE_SQL
    finally:
        reference.close()


def _expected_price_currency_policy_sql() -> dict[str, str]:
    global _EXPECTED_PRICE_CURRENCY_POLICY_SQL
    if _EXPECTED_PRICE_CURRENCY_POLICY_SQL is not None:
        return _EXPECTED_PRICE_CURRENCY_POLICY_SQL
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript("""
            CREATE TABLE marketplace_listings(id INTEGER PRIMARY KEY, platform TEXT NOT NULL);
            CREATE TABLE marketplace_listing_observations(
                id INTEGER PRIMARY KEY,
                listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id),
                observed_at TEXT NOT NULL, price REAL, currency TEXT,
                source_payload_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE facebook_ads(id INTEGER PRIMARY KEY, library_id TEXT);
            CREATE TABLE facebook_ad_observations(
                id INTEGER PRIMARY KEY, ad_id INTEGER, observed_at TEXT
            );
        """)
        root = Path(__file__).resolve().parent
        for filename in ("002_price_evidence.sql", "003_price_evidence_trust_policy.sql"):
            path = root / "migrations" / filename
            pending = ""
            for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
                pending += line
                if not sqlite3.complete_statement(pending):
                    continue
                statement = pending.strip()
                meaningful = any(
                    item.strip() and not item.lstrip().startswith("--")
                    for item in statement.splitlines()
                )
                if meaningful:
                    reference.execute(statement)
                pending = ""
            if pending.strip():
                raise RuntimeError(f"{filename} SQL has an incomplete statement")
        names = {name for group in PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS.values() for name in group}
        names.add("trusted_price_evidence")
        _EXPECTED_PRICE_CURRENCY_POLICY_SQL = {
            str(row[0]): str(row[1])
            for row in reference.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN (%s)" % ",".join("?" * len(names)),
                tuple(sorted(names)),
            )
            if row[1] is not None
        }
        return _EXPECTED_PRICE_CURRENCY_POLICY_SQL
    finally:
        reference.close()


def _missing_objects(
    found: dict[str, str], required: dict[str, set[str]]
) -> list[str]:
    missing: list[str] = []
    for kind, names in required.items():
        for name in sorted(names):
            if found.get(name) != kind:
                missing.append(f"{kind} {name}")
    return missing


def foundation_schema_problems(conn: sqlite3.Connection) -> list[str]:
    found = object_types(conn)
    problems = _missing_objects(
        found,
        {
            "table": set(FOUNDATION_TABLE_COLUMNS) | FOUNDATION_LEGACY_TABLES,
            **FOUNDATION_REQUIRED_OBJECTS,
        },
    )
    for table, required in FOUNDATION_TABLE_COLUMNS.items():
        missing = required - table_columns(conn, table) if found.get(table) == "table" else required
        if missing:
            problems.append(f"columns in {table}: {', '.join(sorted(missing))}")

    expected_sql, expected_table_shapes = _expected_foundation_schema()
    for name, (expected_type, expected_definition) in expected_sql.items():
        actual = conn.execute(
            "SELECT type, sql FROM sqlite_master WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if actual is None or str(actual[0]) != expected_type:
            continue  # The missing/wrong object type is reported above.
        if _canonical_sql(actual[1]) != _canonical_sql(expected_definition):
            kind = "table" if expected_type == "table" else expected_type
            problems.append(f"{kind} definition of {name} differs from Migration 001")
    for table, expected_shape in expected_table_shapes.items():
        if found.get(table) == "table" and _foundation_table_shape(conn, table) != expected_shape:
            problems.append(
                f"columns, constraints, foreign keys, or indexes in {table} differ from Migration 001"
            )
    managed_tables = set(FOUNDATION_TABLE_COLUMNS)
    expected_indexes = FOUNDATION_REQUIRED_OBJECTS["index"]
    expected_triggers = FOUNDATION_REQUIRED_OBJECTS["trigger"]
    for row in conn.execute(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('index','trigger')"
    ):
        kind, name, owner = str(row[0]), _sqlite_ascii_lower(str(row[1])), _sqlite_ascii_lower(str(row[2]))
        if owner not in managed_tables or name.startswith("sqlite_autoindex"):
            continue
        expected = expected_indexes if kind == "index" else expected_triggers
        if name not in expected:
            problems.append(f"unexpected {kind} {name} on Foundation table {owner}")
    return problems


def price_evidence_schema_problems(conn: sqlite3.Connection) -> list[str]:
    found = object_types(conn)
    problems = _missing_objects(found, PRICE_EVIDENCE_REQUIRED_OBJECTS)
    for table, required in PRICE_EVIDENCE_TABLE_COLUMNS.items():
        missing = required - table_columns(conn, table) if found.get(table) == "table" else required
        if missing:
            problems.append(f"columns in {table}: {', '.join(sorted(missing))}")

    expected_sql = _expected_price_evidence_sql()
    currency_policy_sql = _expected_price_currency_policy_sql()
    for name, expected in expected_sql.items():
        actual = conn.execute("SELECT sql FROM sqlite_master WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        valid_definitions = {expected}
        if name == "trusted_price_evidence":
            valid_definitions.add(currency_policy_sql.get(name, ""))
        if actual is None or not any(
            _canonical_sql(actual[0]) == _canonical_sql(definition)
            for definition in valid_definitions
        ):
            problems.append(f"SQL definition of {name} differs from Migration 002")

    expected_foreign_keys = {
        "facebook_landing_page_fetches": {("facebook_ad_observations", "facebook_ad_observation_id", "id")},
        "price_evidence": {
            ("marketplace_listing_observations", "marketplace_observation_id", "id"),
            ("facebook_landing_page_fetches", "facebook_landing_page_fetch_id", "id"),
        },
        "price_evidence_reviews": {("price_evidence", "price_evidence_id", "id")},
    }
    for table, expected in expected_foreign_keys.items():
        actual = {
            (_sqlite_ascii_lower(str(row[2])), _sqlite_ascii_lower(str(row[3])), _sqlite_ascii_lower(str(row[4])))
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        } if found.get(table) == "table" else set()
        if not expected.issubset(actual):
            absent = sorted(expected - actual)
            problems.append(f"foreign keys in {table}: missing {absent}")

    try:
        conn.execute("SELECT * FROM trusted_price_evidence LIMIT 0")
    except sqlite3.Error as exc:
        problems.append(f"trusted_price_evidence view is not queryable: {exc}")
    managed_tables = set(PRICE_EVIDENCE_TABLE_COLUMNS)
    expected_indexes = PRICE_EVIDENCE_REQUIRED_OBJECTS["index"]
    expected_triggers = PRICE_EVIDENCE_REQUIRED_OBJECTS["trigger"] | PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS["trigger"]
    for row in conn.execute(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('index','trigger')"
    ):
        kind, name, owner = str(row[0]), _sqlite_ascii_lower(str(row[1])), _sqlite_ascii_lower(str(row[2]))
        if owner not in managed_tables or name.startswith("sqlite_autoindex"):
            continue
        expected = expected_indexes if kind == "index" else expected_triggers
        if name not in expected:
            problems.append(f"unexpected {kind} {name} on price-evidence table {owner}")
    return problems


def price_evidence_currency_policy_problems(conn: sqlite3.Connection) -> list[str]:
    """Require the forward migration that closes currency and legacy-price trust gaps."""
    problems = price_evidence_schema_problems(conn)
    found = object_types(conn)
    problems.extend(_missing_objects(found, PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS))
    expected = _expected_price_currency_policy_sql()
    for name, definition in expected.items():
        actual = conn.execute("SELECT type, sql FROM sqlite_master WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if actual is None or str(actual[0]) != ("view" if name in ("trusted_price_evidence", "legacy_currency_unconfirmed_observations") else "trigger"):
            problems.append(f"missing or wrong type for Migration 003 object {name}")
        elif _canonical_sql(actual[1]) != _canonical_sql(definition):
            problems.append(f"SQL definition of {name} differs from Migration 003")
    try:
        conn.execute("SELECT * FROM legacy_currency_unconfirmed_observations LIMIT 0")
    except sqlite3.Error as exc:
        problems.append(f"legacy_currency_unconfirmed_observations view is not queryable: {exc}")
    return problems


def _pragma_violations(conn: sqlite3.Connection, pragma: str) -> list[tuple]:
    return [tuple(row) for row in conn.execute(f"PRAGMA {pragma}")]


def schema_readiness_message(problems: Iterable[str], *, database: str) -> str:
    details = "; ".join(problems)
    return (
        "Ouedkniss capture is blocked: Migration 002 (price evidence) or Migration 003 "
        "(currency trust policy) is missing or "
        f"structurally incomplete in the configured database {database}. {details}. "
        "Create and verify a backup, then run the versioned migration preflight and apply "
        "command against this explicitly selected database. The sensor never applies migrations."
    )


__all__ = [
    "FOUNDATION_TABLE_COLUMNS",
    "FOUNDATION_LEGACY_TABLES",
    "FOUNDATION_REQUIRED_OBJECTS",
    "PRICE_EVIDENCE_TABLE_COLUMNS",
    "PRICE_EVIDENCE_REQUIRED_OBJECTS",
    "PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS",
    "object_types",
    "table_columns",
    "foundation_schema_problems",
    "price_evidence_schema_problems",
    "price_evidence_currency_policy_problems",
    "_pragma_violations",
    "schema_readiness_message",
]
