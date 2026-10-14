"""Preflight, apply, inspect, or restore versioned SQLite migrations.

Write commands require an explicit --db path. Applying migrations creates and
independently verifies a pre-migration SQLite online backup for each version.
Migration 002 cannot be rolled back with DDL; restore a verified backup instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from database_safety import enforce_database_target
from migration_lifecycle import (
    MigrationError,
    apply_migrations,
    backup_database,
    inspect_database,
    preflight_database,
    restore_verified_backup,
    rollback_foundation,
)


def apply(path: Path, *, isolated: bool = False) -> Path:
    """Backward-compatible Migration 001 API used by isolated Foundation tests."""
    path = enforce_database_target(path, isolated=isolated)
    run = apply_migrations(path, target_version=1, isolated=isolated)
    if run.backups:
        return Path(run.backups[-1]["path"])
        return backup_database(path, migration_version=1, isolated=isolated)


def rollback(path: Path, *, isolated: bool = False) -> Path:
    """Invoke the rollback command, which currently refuses destructive schema drops."""
    path = enforce_database_target(path, isolated=isolated)
    return rollback_foundation(path, isolated=isolated)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("status", "preflight", "apply", "rollback-001", "restore")
    )
    parser.add_argument("--db", type=Path, required=True, help="explicit SQLite database path")
    parser.add_argument(
        "--isolated-db",
        action="store_true",
        help="explicitly select non-production mode; always refuses the protected production file",
    )
    parser.add_argument("--target", type=int, choices=(1, 2, 3, 4), default=4, help="highest migration version")
    parser.add_argument("--backup", type=Path, help="verified backup path for restore")
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="acknowledge that all writers and connections were stopped before restore",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path = enforce_database_target(args.db, isolated=args.isolated_db)
        if args.command == "status":
            result = inspect_database(path)
        elif args.command == "preflight":
            result = preflight_database(path, target_version=args.target)
        elif args.command == "apply":
            run = apply_migrations(path, target_version=args.target, isolated=args.isolated_db)
            result = {
                "database": run.database,
                "target_version": run.target_version,
                "states": run.states,
                "backups": run.backups,
                "validations": run.validations,
            }
        elif args.command == "rollback-001":
            result = {
                "backup": str(rollback_foundation(path, isolated=args.isolated_db)),
                "rolled_back": "001_foundation_v2",
            }
        else:
            if args.backup is None:
                raise MigrationError("restore requires --backup with a verified pre-migration backup")
            result = restore_verified_backup(
                path,
                args.backup,
                isolated=args.isolated_db,
                writers_stopped=args.writers_stopped,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Migration command failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
