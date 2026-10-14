"""Fail-closed identity checks for migration and recovery command targets."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
# This identity is deliberately independent of KRATZWERK_DB_PATH.
PROTECTED_PRODUCTION_DATABASE_PATH = (
    PROJECT_ROOT / "data" / "Database.sqlite"
).resolve()


class DatabaseTargetError(RuntimeError):
    """A command selected a database outside its explicitly permitted mode."""


def resolve_database_path(path: Path | str) -> Path:
    """Expand and resolve a database path, including existing symlinks/junctions."""
    return Path(path).expanduser().resolve(strict=False)


def same_database_file(left: Path | str, right: Path | str) -> bool:
    """Compare canonical paths and, when possible, the underlying file identity."""
    left_path = resolve_database_path(left)
    right_path = resolve_database_path(right)
    left_key = os.path.normcase(str(left_path))
    right_key = os.path.normcase(str(right_path))
    if os.name == "nt":
        left_key, right_key = left_key.casefold(), right_key.casefold()
    if left_key == right_key:
        return True
    try:
        if left_path.exists() and right_path.exists():
            return os.path.samefile(left_path, right_path)
    except OSError:
        pass
    return False


def enforce_database_target(path: Path | str, *, isolated: bool = False) -> Path:
    """Bind the selected database to the protected target or an explicit isolated mode."""
    resolved = resolve_database_path(path)
    protected = PROTECTED_PRODUCTION_DATABASE_PATH
    matches_protected = same_database_file(resolved, protected)
    if isolated and matches_protected:
        raise DatabaseTargetError(
            "Migration command refused: --isolated-db cannot target the protected production "
            f"database '{protected}'."
        )
    if not isolated and not matches_protected:
        raise DatabaseTargetError(
            "Migration command refused: --db must resolve to the protected production "
            f"database '{protected}'; pass --isolated-db only for a non-production database."
        )
    return resolved

