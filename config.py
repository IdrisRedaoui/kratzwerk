"""Central database configuration for Kratzwerk.

Set KRATZWERK_DB_PATH to point DataGrip and every sensor at the same SQLite
file. Without it, the project uses ./data/Database.sqlite.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from database_safety import PROTECTED_PRODUCTION_DATABASE_PATH

PROJECT_ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(
    os.environ.get("KRATZWERK_DB_PATH", PROJECT_ROOT / "data" / "Database.sqlite")
).expanduser().resolve()


def connect(*, read_only: bool = False, timeout: float = 30.0) -> sqlite3.Connection:
    """Open the configured database with foreign key enforcement enabled."""
    if read_only:
        conn = sqlite3.connect(
            f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True, timeout=timeout
        )
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
