"""Versioned, backup-verified SQLite migration and restore lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import uuid
import sqlite3
import tempfile
from typing import Any, Callable, Mapping

from database_safety import enforce_database_target, same_database_file
from schema_validation import (
    FOUNDATION_LEGACY_TABLES,
    FOUNDATION_REQUIRED_OBJECTS,
    FOUNDATION_TABLE_COLUMNS,
    PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS,
    PRICE_EVIDENCE_REQUIRED_OBJECTS,
    PRICE_EVIDENCE_TABLE_COLUMNS,
    PRICE_EVIDENCE_TRUST_BOUNDARY_COLUMNS,
    PRICE_EVIDENCE_TRUST_BOUNDARY_OBJECTS,
    price_evidence_currency_policy_problems,
    price_evidence_trust_boundary_problems,
    _canonical_sql,
    _sqlite_ascii_lower,
    foundation_schema_problems,
    object_types,
    price_evidence_schema_problems,
)


ROOT = Path(__file__).resolve().parent
MIGRATIONS: dict[int, tuple[str, Path]] = {
    1: ("001_foundation_v2.sql", ROOT / "migrations" / "001_foundation_v2.sql"),
    2: ("002_price_evidence.sql", ROOT / "migrations" / "002_price_evidence.sql"),
    3: ("003_price_evidence_trust_policy.sql", ROOT / "migrations" / "003_price_evidence_trust_policy.sql"),
    4: ("004_closed_price_trust_boundaries.sql", ROOT / "migrations" / "004_closed_price_trust_boundaries.sql"),
}
REGISTRY_TABLE = "schema_migrations"
MIGRATION_001_SOURCE = {
    "Products": "legacy_products",
    "Sellers": "legacy_sellers",
    "Marketplace_Listings": "legacy_marketplace_listings",
    "Price_History": "legacy_price_history",
    "Advertising_Campaigns": "legacy_advertising_campaigns",
}
MIGRATION_002_SOURCE_TABLES = (
    "sellers",
    "marketplace_listings",
    "marketplace_listing_observations",
    "facebook_ads",
    "facebook_ad_observations",
)
LEGACY_TABLES = {
    "products", "sellers", "marketplace_listings", "price_history", "advertising_campaigns"
}

REGISTRY_DDL = """CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT,
    recorded_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('APPLIED', 'BASELINED')),
    verification TEXT NOT NULL,
    CHECK ((state = 'APPLIED' AND applied_at IS NOT NULL)
        OR (state = 'BASELINED' AND applied_at IS NULL))
)"""


class MigrationError(RuntimeError):
    """A migration or its preflight failed safely."""


@dataclass
class MigrationRun:
    database: str
    target_version: int
    states: dict[str, str] = field(default_factory=dict)
    backups: list[dict[str, Any]] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def migration_checksum(version: int) -> str:
    try:
        path = MIGRATIONS[version][1]
    except KeyError as exc:
        raise MigrationError(f"Unknown migration version {version}") from exc
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    return value


def table_fingerprint(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    objects = object_types(conn)
    if objects.get(_sqlite_ascii_lower(table)) != "table":
        raise MigrationError(f"Required source table is missing: {table}")
    # Table identifiers come from internal allowlists, not command-line input.
    rows = [tuple(_json_value(v) for v in row) for row in conn.execute(f'SELECT * FROM "{table}"')]
    canonical_rows = sorted(
        rows,
        key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    raw = json.dumps(
        canonical_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    columns = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')]
    id_fingerprint: str | None = None
    if "id" in columns:
        ids = [row[0] for row in conn.execute(f'SELECT id FROM "{table}" ORDER BY id')]
        id_fingerprint = hashlib.sha256(
            json.dumps(ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return {
        "count": len(rows),
        "fingerprint": hashlib.sha256(raw).hexdigest(),
        "id_fingerprint": id_fingerprint,
    }


def source_fingerprints(
    conn: sqlite3.Connection, tables: tuple[str, ...] = MIGRATION_002_SOURCE_TABLES
) -> dict[str, dict[str, Any]]:
    return {table: table_fingerprint(conn, table) for table in tables}


def check_database_health(conn: sqlite3.Connection) -> dict[str, Any]:
    integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if integrity != ["ok"]:
        raise MigrationError(f"SQLite integrity_check failed: {integrity[:5]}")
    if foreign_keys:
        raise MigrationError(f"SQLite foreign_key_check found violations: {foreign_keys[:5]}")
    return {"integrity_check": integrity[0], "foreign_key_violations": len(foreign_keys)}


def _schema_state_snapshot(path: Path) -> dict[str, Any]:
    state = inspect_database(path)
    if any(state[key] == "PARTIAL" for key in (
        "foundation_001", "price_evidence_002", "price_evidence_003", "price_evidence_004"
    )):
        raise MigrationError("Cannot create a provenance manifest for a partial or ambiguous schema")
    return {
        "foundation_001": state["foundation_001"],
        "price_evidence_002": state["price_evidence_002"],
        "price_evidence_003": state["price_evidence_003"],
        "price_evidence_004": state["price_evidence_004"],
        "user_version": state["user_version"],
        "migrations": [
            {
                key: row[key]
                for key in ("version", "filename", "checksum", "applied_at", "state")
            }
            for row in state["migrations"]
        ],
    }


def _manifest_schema_state_matches(
    actual: Mapping[str, Any], recorded: Any
) -> bool:
    if recorded == dict(actual):
        return True
    # Format-v1 manifests created before Migration 004 did not contain this key.
    # Accept that signed shape only when the inspected backup has no Migration 004.
    if isinstance(recorded, dict) and "price_evidence_004" not in recorded:
        compatible = dict(actual)
        if compatible.get("price_evidence_004") == "ABSENT":
            compatible.pop("price_evidence_004", None)
            return recorded == compatible
    return False


def _manifest_path(backup: Path) -> Path:
    return Path(str(backup) + ".manifest.json")


def _manifest_key_path() -> Path:
    """Return a per-user key location outside the database and backup directory."""
    if os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return base / "Kratzwerk" / "backup-manifest.key"


def _manifest_signing_key() -> bytes:
    """Read the existing local manifest key without ever creating replacement material."""
    path = _manifest_key_path()
    try:
        key = path.read_bytes()
    except FileNotFoundError as exc:
        raise MigrationError(
            f"Local backup-manifest signing key is missing: {path}; verification cannot continue"
        ) from exc
    except OSError as exc:
        raise MigrationError(f"Local backup-manifest signing key cannot be read at {path}: {exc}") from exc
    if len(key) != 32:
        raise MigrationError(f"Local backup-manifest signing key is invalid at {path}; expected 32 bytes")
    return key


def initialize_backup_manifest_signing_key() -> Path:
    """Explicitly create the local signing key required for new signed backups.

    Existing key material is never replaced. If a valid key already exists, its
    path is returned; invalid existing material raises and remains untouched.
    """
    path = _manifest_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(secrets.token_bytes(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        _manifest_signing_key()
    return path


def _manifest_signature(manifest: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "signature_hmac_sha256"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_manifest_signing_key(), canonical, hashlib.sha256).hexdigest()


def backup_database(
    path: Path,
    *,
    migration_version: int = 4,
    isolated: bool = False,
) -> Path:
    """Create a consistent online backup to a separate, atomically installed file."""
    path = enforce_database_target(path, isolated=isolated)
    # Provisioning is a separate explicit operation. A backup never creates or
    # replaces the key it needs to sign its manifest.
    _manifest_signing_key()
    if not path.is_file():
        raise FileNotFoundError(f"Database does not exist: {path}")
    schema_state = _schema_state_snapshot(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    final = path.with_name(f"{path.name}.pre-migration-v{migration_version:03d}.{stamp}.bak")
    if final.resolve() == path:
        raise MigrationError("Backup path must differ from the database path")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{final.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    temporary = Path(temp_name)
    try:
        source = _connect_read_only(path)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        # Independently open and validate the completed backup before publishing it.
        verify_backup(temporary)
        os.replace(temporary, final)
        manifest = {
            "format_version": 1,
            "backup_id": str(uuid.uuid4()),
            "backup_filename": final.name,
            "backup_sha256": sha256_file(final),
            "intended_target": str(path),
            "intended_target_identity": os.path.normcase(str(path)),
            "migration_version_purpose": migration_version,
            "schema_state": schema_state,
            "created_at": utc_now(),
        }
        manifest["signature_hmac_sha256"] = _manifest_signature(manifest)
        manifest_path = _manifest_path(final)
        temp_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
        try:
            temp_manifest.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(temp_manifest, manifest_path)
        finally:
            temp_manifest.unlink(missing_ok=True)
        verify_backup(final, require_manifest=True, expected_target=path)
        return final
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            _manifest_path(final).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def verify_backup(
    backup: Path,
    *,
    expected_fingerprints: Mapping[str, Mapping[str, Any]] | None = None,
    require_manifest: bool = False,
    expected_target: Path | None = None,
) -> dict[str, Any]:
    backup = backup.expanduser().resolve()
    if not backup.is_file():
        raise MigrationError(f"Backup does not exist: {backup}")
    conn = _connect_read_only(backup)
    try:
        health = check_database_health(conn)
        fingerprints: dict[str, dict[str, Any]] = {}
        if expected_fingerprints is not None:
            fingerprints = source_fingerprints(conn, tuple(expected_fingerprints))
            if fingerprints != dict(expected_fingerprints):
                raise MigrationError("Backup source-table counts/fingerprints do not match baseline")
        result = {
            "path": str(backup),
            "sha256": sha256_file(backup),
            **health,
            "source_fingerprints": fingerprints,
        }
        manifest_path = _manifest_path(backup)
        if not manifest_path.is_file():
            if require_manifest:
                raise MigrationError("Backup provenance manifest is missing; use a verified manual recovery source")
            return result
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError(f"Backup provenance manifest is unreadable: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
            raise MigrationError("Backup provenance manifest has an unsupported format")
        signature = manifest.get("signature_hmac_sha256")
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, _manifest_signature(manifest)
        ):
            raise MigrationError("Backup provenance manifest signature is invalid")
        if manifest.get("backup_filename") != backup.name:
            raise MigrationError("Backup provenance manifest names a different backup file")
        if manifest.get("backup_sha256") != result["sha256"]:
            raise MigrationError("Backup content hash does not match its provenance manifest")
        target_text = manifest.get("intended_target")
        if not isinstance(target_text, str) or not target_text:
            raise MigrationError("Backup provenance manifest has no intended target identity")
        expected_identity = os.path.normcase(str(Path(target_text).expanduser().resolve(strict=False)))
        if manifest.get("intended_target_identity") != expected_identity:
            raise MigrationError("Backup provenance manifest target identity is inconsistent")
        if expected_target is not None and not same_database_file(target_text, expected_target):
            raise MigrationError("Backup was created for a different database target")
        actual_state = _schema_state_snapshot(backup)
        if not _manifest_schema_state_matches(actual_state, manifest.get("schema_state")):
            raise MigrationError("Backup schema or migration state does not match its provenance manifest")
        result["manifest"] = manifest
        return result
    finally:
        conn.close()


def _sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            meaningful = any(
                line.strip() and not line.lstrip().startswith("--")
                for line in statement.splitlines()
            )
            if statement and meaningful:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise MigrationError("Migration SQL ends with an incomplete statement")
    return statements


def _run_script(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fail_after_statement: int | None = None,
) -> int:
    statements = _sql_statements(path.read_text(encoding="utf-8"))
    if fail_after_statement is not None and fail_after_statement < 1:
        raise ValueError("fail_after_statement must be positive")
    for index, statement in enumerate(statements, start=1):
        conn.execute(statement)
        if fail_after_statement == index:
            raise RuntimeError(f"Injected migration failure after SQL statement {index}")
    return len(statements)


def _foundation_state(conn: sqlite3.Connection) -> tuple[str, list[str]]:
    problems = foundation_schema_problems(conn)
    if not problems:
        return "APPLIED", []
    found = object_types(conn)
    legacy = LEGACY_TABLES.issubset(found)
    # products, sellers, and marketplace_listings share ASCII-insensitive names
    # with the legacy source tables before Migration 001 renames them. They do
    # not by themselves prove that any Foundation objects were applied.
    v2_names = (set(FOUNDATION_TABLE_COLUMNS) - {"products", "sellers", "marketplace_listings"}) | {
        name for values in FOUNDATION_REQUIRED_OBJECTS.values() for name in values
    }
    has_v2_remnants = any(name in found for name in v2_names)
    if legacy and not has_v2_remnants:
        return "ABSENT", problems
    if not legacy and not has_v2_remnants:
        return "ABSENT", problems
    return "PARTIAL", problems


def _price_state(conn: sqlite3.Connection) -> tuple[str, list[str]]:
    problems = price_evidence_schema_problems(conn)
    if not problems:
        return "APPLIED", []
    found = object_types(conn)
    expected_names = {
        name for group in PRICE_EVIDENCE_REQUIRED_OBJECTS.values() for name in group
    }
    if any(name in found for name in expected_names):
        return "PARTIAL", problems
    return "ABSENT", problems


def _price_currency_policy_state(conn: sqlite3.Connection) -> tuple[str, list[str]]:
    problems = price_evidence_currency_policy_problems(conn)
    if not problems:
        return "APPLIED", []
    found = object_types(conn)
    expected_names = {
        name for names in PRICE_EVIDENCE_CURRENCY_POLICY_OBJECTS.values() for name in names
    }
    if any(name in found for name in expected_names):
        return "PARTIAL", problems
    return "ABSENT", problems


def _price_trust_boundary_state(conn: sqlite3.Connection) -> tuple[str, list[str]]:
    problems = price_evidence_trust_boundary_problems(conn)
    if not problems:
        return "APPLIED", []
    found = object_types(conn)
    expected_names = PRICE_EVIDENCE_TRUST_BOUNDARY_OBJECTS["trigger"] - {
        "trg_price_evidence_reviews_authorization"
    }
    if any(name in found for name in expected_names):
        return "PARTIAL", problems
    columns = {
        str(row[1])
        for table in PRICE_EVIDENCE_TRUST_BOUNDARY_COLUMNS
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    }
    if columns & {column for names in PRICE_EVIDENCE_TRUST_BOUNDARY_COLUMNS.values() for column in names}:
        return "PARTIAL", problems
    return "ABSENT", problems


def _registry_state(conn: sqlite3.Connection) -> tuple[bool, dict[int, dict[str, Any]]]:
    found = object_types(conn)
    if REGISTRY_TABLE not in found:
        return False, {}
    if found[REGISTRY_TABLE] != "table":
        raise MigrationError(f"Object-name collision: {REGISTRY_TABLE} is not a table")
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(schema_migrations)")}
    expected = {"version", "filename", "checksum", "applied_at", "recorded_at", "state", "verification"}
    if not expected.issubset(columns):
        raise MigrationError(
            "schema_migrations exists with an incomplete shape; preserve it and recover from a verified backup"
        )
    actual_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_migrations' COLLATE NOCASE"
    ).fetchone()
    actual_sql = _canonical_sql(str(actual_sql_row[0] or "")) if actual_sql_row else ""
    expected_sql = _canonical_sql(REGISTRY_DDL)
    if actual_sql != expected_sql:
        raise MigrationError(
            "schema_migrations has an unexpected structure or constraints; preserve it and recover "
            "from a verified backup"
        )
    rows: dict[int, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT version, filename, checksum, applied_at, recorded_at, state, verification "
        "FROM schema_migrations ORDER BY version"
    ):
        version = int(row[0])
        if version not in MIGRATIONS:
            raise MigrationError(f"Unknown migration version {version} in schema_migrations")
        rows[version] = {
            "version": version,
            "filename": str(row[1]),
            "checksum": str(row[2]),
            "applied_at": row[3],
            "recorded_at": str(row[4]),
            "state": str(row[5]),
            "verification": str(row[6]),
        }
    return True, rows


def _check_registry_consistency(
    rows: Mapping[int, Mapping[str, Any]], foundation: str, price: str,
    currency_policy: str, trust_boundary: str,
) -> None:
    for version in rows:
        if version > 1 and any(previous not in rows for previous in range(1, version)):
            raise MigrationError(
                f"Migration registry is inconsistent: version {version:03d} is recorded without its predecessors"
            )
    for version, schema_state in (
        (1, foundation), (2, price), (3, currency_policy), (4, trust_boundary)
    ):
        row = rows.get(version)
        if row is None:
            continue
        expected_filename, _ = MIGRATIONS[version]
        if row["filename"] != expected_filename:
            raise MigrationError(
                f"Migration {version:03d} filename drift: registry has {row['filename']!r}; "
                f"expected {expected_filename!r}"
            )
        expected_checksum = migration_checksum(version)
        if row["checksum"] != expected_checksum:
            raise MigrationError(
                f"Migration {version:03d} checksum drift detected for an already recorded migration. "
                "Do not edit the applied SQL; restore a verified backup or add a new migration version."
            )
        if schema_state != "APPLIED":
            raise MigrationError(
                f"Migration {version:03d} is recorded as {row['state']} but its schema is {schema_state}. "
                "Do not delete objects manually; restore a verified backup and inspect the database."
            )


def inspect_database(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database does not exist: {path}")
    conn = _connect_read_only(path)
    try:
        foundation, foundation_problems = _foundation_state(conn)
        price, price_problems = _price_state(conn)
        currency_policy, currency_policy_problems = _price_currency_policy_state(conn)
        trust_boundary, trust_boundary_problems = _price_trust_boundary_state(conn)
        has_registry, registry = _registry_state(conn)
        _check_registry_consistency(registry, foundation, price, currency_policy, trust_boundary)
        health = check_database_health(conn)
        return {
            "database": str(path),
            "foundation_001": foundation,
            "foundation_problems": foundation_problems,
            "price_evidence_002": price,
            "price_evidence_problems": price_problems,
            "price_evidence_003": currency_policy,
            "price_evidence_003_problems": currency_policy_problems,
            "price_evidence_004": trust_boundary,
            "price_evidence_004_problems": trust_boundary_problems,
            "registry_present": has_registry,
            "migrations": [registry[v] for v in sorted(registry)],
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            **health,
        }
    finally:
        conn.close()


def preflight_database(path: Path, *, target_version: int = 4) -> dict[str, Any]:
    if target_version not in MIGRATIONS:
        raise MigrationError(f"Unsupported target migration {target_version}")
    result = inspect_database(path)
    if result["foundation_001"] == "PARTIAL":
        raise MigrationError(
            "Foundation V2 has partial or structurally incomplete objects: "
            + "; ".join(result["foundation_problems"])
            + ". No objects were removed. Restore a verified pre-migration backup or repair from a reviewed copy."
        )
    if result["foundation_001"] == "ABSENT":
        conn = _connect_read_only(Path(result["database"]))
        try:
            found = object_types(conn)
            missing = sorted(LEGACY_TABLES - set(found))
            if missing:
                raise MigrationError(
                    "Unrecognized database schema; Foundation V2 is absent and legacy source tables "
                    f"are missing: {missing}"
                )
            all_001_names = (set(FOUNDATION_TABLE_COLUMNS) - {
                "products", "sellers", "marketplace_listings"
            }) | {
                name for names in FOUNDATION_REQUIRED_OBJECTS.values() for name in names
            }
            v2_name_collisions = sorted(name for name in all_001_names if name in found)
            if v2_name_collisions:
                raise MigrationError(
                    f"Preflight found object-name collisions: {v2_name_collisions}. No changes made."
                )
        finally:
            conn.close()
    if result["price_evidence_002"] == "PARTIAL":
        raise MigrationError(
            "Migration 002 has partial or structurally incomplete objects: "
            + "; ".join(result["price_evidence_problems"])
            + ". No objects were removed. Restore a verified pre-migration backup or inspect a separate copy."
        )
    if result["price_evidence_003"] == "PARTIAL":
        raise MigrationError(
            "Migration 003 has partial or structurally incomplete objects: "
            + "; ".join(result["price_evidence_003_problems"])
            + ". No objects were removed. Restore a verified pre-migration backup or inspect a separate copy."
        )
    if result["price_evidence_004"] == "PARTIAL":
        raise MigrationError(
            "Migration 004 has partial or structurally incomplete trust-boundary objects: "
            + "; ".join(result["price_evidence_004_problems"])
            + ". No objects were removed. Restore a verified pre-migration backup or inspect a separate copy."
        )
    if target_version >= 2 and result["foundation_001"] != "APPLIED" and result["foundation_001"] != "ABSENT":
        raise MigrationError("Migration 002 requires a complete Foundation V2 schema")
    if (
        target_version >= 3
        and result["price_evidence_003"] == "APPLIED"
        and result["price_evidence_002"] != "APPLIED"
    ):
        raise MigrationError("Migration 003 cannot be present without a complete Migration 002 schema")
    if target_version >= 4 and result["price_evidence_004"] == "APPLIED":
        if result["price_evidence_003"] != "APPLIED" or result["price_evidence_002"] != "APPLIED":
            raise MigrationError("Migration 004 cannot be present without complete Migrations 002 and 003")
    return result


def _source_map_for_001_before(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {name: table_fingerprint(conn, name) for name in MIGRATION_001_SOURCE}


def _source_map_for_001_after(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {name: table_fingerprint(conn, target) for name, target in MIGRATION_001_SOURCE.items()}


def _validate_transaction_state(
    conn: sqlite3.Connection,
    *,
    expected_sources: Mapping[str, Mapping[str, Any]] | None = None,
    source_mapper: Callable[[sqlite3.Connection], dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    health = check_database_health(conn)
    if expected_sources is not None and source_mapper is not None:
        actual = source_mapper(conn)
        if actual != dict(expected_sources):
            raise MigrationError("Source table row/ID fingerprints changed during migration")
    return health


def _create_verified_backup(
    path: Path,
    version: int,
    source_tables: tuple[str, ...] | None = None,
    *,
    isolated: bool = False,
) -> tuple[Path, dict[str, Any]]:
    before_conn = _connect_read_only(path)
    try:
        before = source_fingerprints(before_conn, source_tables) if source_tables else None
        health = check_database_health(before_conn)
    finally:
        before_conn.close()
    backup = backup_database(path, migration_version=version, isolated=isolated)
    verification = verify_backup(backup, expected_fingerprints=before)
    verification["baseline_health"] = health
    return backup, verification


def _all_user_table_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = tuple(sorted(
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ))
    return source_fingerprints(conn, tables)


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if enabled != 1:
        raise MigrationError("SQLite foreign-key enforcement could not be enabled before the transaction")
    conn.execute("BEGIN IMMEDIATE")


def _assert_matches_backup(
    conn: sqlite3.Connection,
    backup: Path,
    source_tables: tuple[str, ...] | None,
) -> None:
    verify_backup(backup)
    backup_conn = _connect_read_only(backup)
    try:
        expected_source = (
            source_fingerprints(backup_conn, source_tables)
            if source_tables is not None
            else _all_user_table_fingerprints(backup_conn)
        )
    finally:
        backup_conn.close()
    live = (
        source_fingerprints(conn, source_tables)
        if source_tables is not None
        else _all_user_table_fingerprints(conn)
    )
    if live != expected_source:
        raise MigrationError(
            "Database source rows changed after the backup was created. Stop writers, take a fresh "
            "backup, and rerun preflight."
        )


def _ensure_registry(conn: sqlite3.Connection) -> None:
    found = object_types(conn)
    if REGISTRY_TABLE not in found:
        conn.execute(REGISTRY_DDL)
        return
    if found[REGISTRY_TABLE] != "table":
        raise MigrationError(f"Object-name collision: {REGISTRY_TABLE} is not a table")
    _registry_state(conn)


def _record_migration(
    conn: sqlite3.Connection,
    version: int,
    *,
    state: str,
    applied_at: str | None,
    verification: str,
) -> None:
    filename, _ = MIGRATIONS[version]
    conn.execute(
        """INSERT INTO schema_migrations
           (version, filename, checksum, applied_at, recorded_at, state, verification)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (version, filename, migration_checksum(version), applied_at, utc_now(), state, verification),
    )


def _write_registry_baselines(
    path: Path,
    versions: list[int],
    run: MigrationRun,
    *,
    isolated: bool,
) -> None:
    if not versions:
        return
    baseline_conn = _connect_read_only(path)
    try:
        foundation, foundation_problems = _foundation_state(baseline_conn)
        price, price_problems = _price_state(baseline_conn)
        currency_policy, currency_policy_problems = _price_currency_policy_state(baseline_conn)
        trust_boundary, trust_boundary_problems = _price_trust_boundary_state(baseline_conn)
        requirements = {
            1: (foundation == "APPLIED", foundation_problems),
            2: (foundation == "APPLIED" and price == "APPLIED", price_problems),
            3: (
                foundation == "APPLIED" and price == "APPLIED" and currency_policy == "APPLIED",
                currency_policy_problems,
            ),
            4: (
                foundation == "APPLIED" and price == "APPLIED"
                and currency_policy == "APPLIED" and trust_boundary == "APPLIED",
                trust_boundary_problems,
            ),
        }
        for version in versions:
            valid, problems = requirements[version]
            if not valid:
                raise MigrationError(
                    f"Cannot baseline Migration {version:03d} before its complete schema is verified: "
                    + "; ".join(problems)
                )
        baseline = _all_user_table_fingerprints(baseline_conn)
        baseline_health = check_database_health(baseline_conn)
    finally:
        baseline_conn.close()
    backup, backup_info = _create_verified_backup(path, min(versions), isolated=isolated)
    backup_check = verify_backup(backup, expected_fingerprints=baseline)
    backup_info.update(backup_check)
    backup_info["baseline_health"] = baseline_health
    run.backups.append(backup_info)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    try:
        _begin(conn)
        _assert_matches_backup(conn, backup, None)
        _ensure_registry(conn)
        foundation, _ = _foundation_state(conn)
        price, _ = _price_state(conn)
        currency_policy, _ = _price_currency_policy_state(conn)
        trust_boundary, _ = _price_trust_boundary_state(conn)
        for version in versions:
            if version == 1 and foundation != "APPLIED":
                raise MigrationError("Cannot baseline Migration 001 without a complete Foundation V2 schema")
            if version == 2 and (foundation != "APPLIED" or price != "APPLIED"):
                raise MigrationError("Cannot baseline Migration 002 without its complete schema")
            if version == 3 and (
                foundation != "APPLIED" or price != "APPLIED" or currency_policy != "APPLIED"
            ):
                raise MigrationError("Cannot baseline Migration 003 without its complete schema")
            if version == 4 and (
                foundation != "APPLIED" or price != "APPLIED"
                or currency_policy != "APPLIED" or trust_boundary != "APPLIED"
            ):
                raise MigrationError("Cannot baseline Migration 004 without its complete schema")
            _record_migration(
                conn,
                version,
                state="BASELINED",
                applied_at=None,
                verification=(
                    "Schema structurally verified by the versioned runner; original application "
                    "timestamp is unknown."
                ),
            )
        check_database_health(conn)
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    run.validations.append({"registry_baselines_recorded": versions, "state": "PASS"})


def _apply_one(
    path: Path,
    version: int,
    run: MigrationRun,
    *,
    isolated: bool,
    fail_after_statement: int | None = None,
) -> None:
    filename, sql_path = MIGRATIONS[version]
    sources_before: dict[str, dict[str, Any]] | None = None
    source_tables: tuple[str, ...] | None = None
    if version == 1:
        source_tables = tuple(MIGRATION_001_SOURCE)
        source_conn = _connect_read_only(path)
        try:
            sources_before = _source_map_for_001_before(source_conn)
        finally:
            source_conn.close()
    elif version in (2, 3, 4):
        source_tables = MIGRATION_002_SOURCE_TABLES
        source_conn = _connect_read_only(path)
        try:
            sources_before = source_fingerprints(source_conn, source_tables)
        finally:
            source_conn.close()
    backup, backup_info = _create_verified_backup(path, version, source_tables, isolated=isolated)
    run.backups.append(backup_info)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    try:
        _begin(conn)
        _assert_matches_backup(conn, backup, source_tables)
        _ensure_registry(conn)
        _registry_exists, rows = _registry_state(conn)
        if version in rows:
            raise MigrationError(f"Migration {version:03d} became recorded while applying; rerun status")
        if version == 1:
            foundation, _ = _foundation_state(conn)
            if foundation != "ABSENT":
                raise MigrationError(f"Foundation preflight changed during migration: state={foundation}")
        else:
            foundation, foundation_problems = _foundation_state(conn)
            price, price_problems = _price_state(conn)
            if foundation != "APPLIED":
                raise MigrationError("Migration 002 requires a complete Foundation V2 schema")
            currency_policy, currency_policy_problems = _price_currency_policy_state(conn)
            trust_boundary, trust_boundary_problems = _price_trust_boundary_state(conn)
            if version == 2 and price != "ABSENT":
                raise MigrationError(
                    "Migration 002 object set changed during preflight: "
                    + "; ".join(price_problems)
                )
            if version == 3 and price != "APPLIED":
                raise MigrationError("Migration 003 requires a complete Migration 002 schema")
            if version == 3 and currency_policy != "ABSENT":
                raise MigrationError(
                    "Migration 003 object set changed during preflight: "
                    + "; ".join(currency_policy_problems)
                )
            if version == 4 and currency_policy != "APPLIED":
                raise MigrationError("Migration 004 requires a complete Migration 003 schema")
            if version == 4 and trust_boundary != "ABSENT":
                raise MigrationError(
                    "Migration 004 object set changed during preflight: "
                    + "; ".join(trust_boundary_problems)
                )
        _run_script(conn, sql_path, fail_after_statement=fail_after_statement)
        if version == 1:
            foundation, problems = _foundation_state(conn)
            if foundation != "APPLIED":
                raise MigrationError("Migration 001 postflight is incomplete: " + "; ".join(problems))
            current_sources = _source_map_for_001_after(conn)
            if current_sources != sources_before:
                raise MigrationError("Migration 001 changed its source-table row/ID fingerprints")
        elif version == 2:
            price, problems = _price_state(conn)
            if price != "APPLIED":
                raise MigrationError("Migration 002 postflight is incomplete: " + "; ".join(problems))
            current_sources = source_fingerprints(conn, MIGRATION_002_SOURCE_TABLES)
            if current_sources != sources_before:
                raise MigrationError("Migration 002 changed source-table row/ID fingerprints")
        elif version == 3:
            currency_policy, problems = _price_currency_policy_state(conn)
            if currency_policy != "APPLIED":
                raise MigrationError("Migration 003 postflight is incomplete: " + "; ".join(problems))
            current_sources = source_fingerprints(conn, MIGRATION_002_SOURCE_TABLES)
            if current_sources != sources_before:
                raise MigrationError("Migration 003 changed source-table row/ID fingerprints")
        else:
            trust_boundary, problems = _price_trust_boundary_state(conn)
            if trust_boundary != "APPLIED":
                raise MigrationError("Migration 004 postflight is incomplete: " + "; ".join(problems))
            current_sources = source_fingerprints(conn, MIGRATION_002_SOURCE_TABLES)
            if current_sources != sources_before:
                raise MigrationError("Migration 004 changed source-table row/ID fingerprints")
        health = check_database_health(conn)
        # The applied record is the final transactional change, after all schema/data checks pass.
        _record_migration(
            conn,
            version,
            state="APPLIED",
            applied_at=utc_now(),
            verification=(
                f"Migration SQL checksum verified; schema, integrity, foreign keys, and source "
                f"fingerprints passed before commit. integrity_check={health['integrity_check']}; "
                f"foreign_key_violations={health['foreign_key_violations']}."
            ),
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()

    verify_conn = _connect_read_only(path)
    try:
        health = check_database_health(verify_conn)
        if version == 1:
            state, problems = _foundation_state(verify_conn)
            sources = _source_map_for_001_after(verify_conn)
        elif version == 2:
            state, problems = _price_state(verify_conn)
            sources = source_fingerprints(verify_conn, MIGRATION_002_SOURCE_TABLES)
        elif version == 3:
            state, problems = _price_currency_policy_state(verify_conn)
            sources = source_fingerprints(verify_conn, MIGRATION_002_SOURCE_TABLES)
        else:
            state, problems = _price_trust_boundary_state(verify_conn)
            sources = source_fingerprints(verify_conn, MIGRATION_002_SOURCE_TABLES)
        if state != "APPLIED" or (sources_before is not None and sources != sources_before):
            raise MigrationError(
                f"Migration {version:03d} committed but read-only post-commit verification failed: "
                + "; ".join(problems)
            )
        _present, rows = _registry_state(verify_conn)
        if version not in rows or rows[version]["checksum"] != migration_checksum(version):
            raise MigrationError(f"Migration {version:03d} registry verification failed after commit")
    finally:
        verify_conn.close()
    run.validations.append({
        "migration": f"{version:03d}",
        "schema": "PASS",
        "source_fingerprints_unchanged": True,
        **health,
    })
    run.states[filename] = "APPLIED"


def apply_migrations(
    path: Path,
    *,
    target_version: int = 4,
    fail_after_statement: int | None = None,
    isolated: bool = False,
) -> MigrationRun:
    """Preflight, back up, then apply each requested migration in its own transaction."""
    path = enforce_database_target(path, isolated=isolated)
    preflight_database(path, target_version=target_version)
    run = MigrationRun(database=str(path), target_version=target_version)
    snapshot = inspect_database(path)
    foundation = snapshot["foundation_001"]
    price = snapshot["price_evidence_002"]
    currency_policy = snapshot["price_evidence_003"]
    trust_boundary = snapshot["price_evidence_004"]
    conn = _connect_read_only(path)
    try:
        _has_registry, registry = _registry_state(conn)
    finally:
        conn.close()
    _check_registry_consistency(registry, foundation, price, currency_policy, trust_boundary)

    if foundation == "ABSENT":
        _apply_one(
            path, 1, run, isolated=isolated,
            fail_after_statement=fail_after_statement if target_version == 1 else None,
        )
    else:
        run.states[MIGRATIONS[1][0]] = registry.get(1, {}).get("state", "BASELINED")
        if 1 not in registry:
            _write_registry_baselines(path, [1], run, isolated=isolated)

    if target_version >= 2:
        for version in (2, 3, 4):
            if version > target_version:
                break
            snapshot = inspect_database(path)
            schema_state = snapshot[
                {2: "price_evidence_002", 3: "price_evidence_003", 4: "price_evidence_004"}[version]
            ]
            if schema_state == "APPLIED":
                conn = _connect_read_only(path)
                try:
                    _has_registry, registry = _registry_state(conn)
                finally:
                    conn.close()
                if version not in registry:
                    _write_registry_baselines(path, [version], run, isolated=isolated)
                    conn = _connect_read_only(path)
                    try:
                        _has_registry, registry = _registry_state(conn)
                    finally:
                        conn.close()
                run.states[MIGRATIONS[version][0]] = registry[version]["state"]
            else:
                _apply_one(
                    path, version, run, isolated=isolated,
                    fail_after_statement=fail_after_statement,
                )

    for version in range(target_version + 1, len(MIGRATIONS) + 1):
        run.states.pop(MIGRATIONS[version][0], None)
    return run


def rollback_foundation(path: Path, *, isolated: bool = False) -> Path:
    """Refuse destructive schema rollback until a separately verified procedure exists."""
    path = enforce_database_target(path, isolated=isolated)
    state = inspect_database(path)
    if state["price_evidence_002"] != "ABSENT":
        raise MigrationError(
            "Schema rollback is not supported after Migration 002. Stop writers and restore a "
            "verified pre-migration backup; do not drop price-evidence objects manually."
        )
    raise MigrationError(
        "Foundation V2 rollback is disabled: no independently verified destructive rollback "
        "procedure is available. Stop writers and restore a verified pre-migration backup instead."
    )


def _copy_backup_to_restore_stage(backup: Path, stage: Path) -> None:
    """Copy a verified source into the already-created staging file."""
    src = _connect_read_only(backup)
    dst = sqlite3.connect(stage)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def restore_verified_backup(
    path: Path,
    backup: Path,
    *,
    isolated: bool = False,
    writers_stopped: bool = False,
) -> dict[str, Any]:
    """Restore only a manifest-bound backup after the operator confirms writers stopped."""
    path = enforce_database_target(path, isolated=isolated)
    backup = backup.expanduser().resolve()
    if writers_stopped is not True:
        raise MigrationError(
            "Restore requires --writers-stopped acknowledgment; this confirms an operational precondition "
            "but does not prove that no writer is connected."
        )
    if path == backup or same_database_file(path, backup):
        raise MigrationError("Restore source and target must be different files")
    if not path.is_file():
        raise FileNotFoundError(f"Restore target does not exist: {path}")
    backup_info = verify_backup(backup, require_manifest=True, expected_target=path)
    manifest = backup_info["manifest"]

    stage_fd, stage_name = tempfile.mkstemp(
        prefix=f".{path.name}.restore-stage-", suffix=".sqlite", dir=str(path.parent)
    )
    os.close(stage_fd)
    stage = Path(stage_name)
    token = f"restore-{os.getpid()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    preserved = path.with_name(f"{path.name}.{token}.previous")
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]
    preserved_sidecars = [Path(str(preserved) + suffix) for suffix in ("-wal", "-shm")]
    moved: list[tuple[Path, Path]] = []
    try:
        _copy_backup_to_restore_stage(backup, stage)
        staged_info = verify_backup(stage)
        staged_state = _schema_state_snapshot(stage)
        if not _manifest_schema_state_matches(staged_state, manifest["schema_state"]):
            raise MigrationError("Staged backup migration state does not match its provenance manifest")

        # This rename fails on Windows if any process still has the database open.
        os.replace(path, preserved)
        moved.append((preserved, path))
        for sidecar, saved in zip(sidecars, preserved_sidecars):
            if sidecar.exists():
                os.replace(sidecar, saved)
                moved.append((saved, sidecar))
        os.replace(stage, path)
        final_info = verify_backup(path)
    except Exception as exc:
        recovery_errors: list[str] = []
        for saved, original in reversed(moved):
            if saved.exists():
                try:
                    os.replace(saved, original)
                except OSError as recovery_exc:
                    recovery_errors.append(
                        f"could not restore {saved} to {original}: {recovery_exc}"
                    )
        try:
            stage.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            recovery_errors.append(f"could not remove restore stage {stage}: {cleanup_exc}")
        if recovery_errors:
            raise MigrationError(
                f"Restore failed: {exc}; recovery/cleanup problems: " + "; ".join(recovery_errors)
            ) from exc
        raise

    return {
        "target": str(path),
        "backup": str(backup),
        "backup_sha256": backup_info["sha256"],
        "restored_sha256": final_info["sha256"],
        "staged_sha256": staged_info["sha256"],
        "integrity_check": final_info["integrity_check"],
        "foreign_key_violations": final_info["foreign_key_violations"],
        "previous_database_preserved_at": str(preserved),
        "preserved_wal_shm": [str(p) for p in preserved_sidecars if p.exists()],
        "backup_id": manifest["backup_id"],
        "manifest_schema_state": manifest["schema_state"],
    }


__all__ = [
    "MigrationError", "MigrationRun", "MIGRATIONS", "REGISTRY_TABLE",
    "migration_checksum", "sha256_file", "table_fingerprint", "source_fingerprints",
    "check_database_health", "backup_database", "verify_backup", "inspect_database",
    "preflight_database", "apply_migrations", "rollback_foundation", "restore_verified_backup",
    "initialize_backup_manifest_signing_key",
]
