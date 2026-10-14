"""Recover Ouedkniss evidence from immutable observations in an isolated database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

import config
from database_safety import (
    PROTECTED_PRODUCTION_DATABASE_PATH,
    resolve_database_path,
    same_database_file,
)
from migration_lifecycle import check_database_health
from price_evidence.recovery import recover_ouedkniss_price_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="explicit isolated SQLite copy")
    args = parser.parse_args(argv)
    path = resolve_database_path(args.db)
    configured = resolve_database_path(config.DATABASE_PATH)
    if same_database_file(path, PROTECTED_PRODUCTION_DATABASE_PATH):
        print(
            "Recovery refused: the protected production database cannot be used, regardless "
            "of KRATZWERK_DB_PATH; use a separate isolated copy.",
            file=sys.stderr,
        )
        return 2
    if same_database_file(path, configured):
        print(
            "Recovery refused: use a separate isolated copy of the configured database.",
            file=sys.stderr,
        )
        return 2
    if not path.is_file():
        print(f"Recovery database does not exist: {path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = recover_ouedkniss_price_evidence(conn)
        result["database"] = str(path)
        result["database_health"] = check_database_health(conn)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["failed"] == 0 and result["source_observations_unchanged"] else 1
    except Exception as exc:
        print(f"Historical price evidence recovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
