"""Explicit deterministic Normalization engine entry points."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Iterator, Sequence

from .persistence import (
    LoadedObservation,
    ProcessResult,
    load_source_observation,
    persist_observation_result,
)
from .versioning import DEFAULT_VERSIONS, EngineVersions


@dataclass(frozen=True)
class NormalizationEngine:
    """Process observations only on a connection explicitly supplied by a caller.

    The engine never opens the configured production database, runs a capture,
    creates products, aliases, or mappings, or applies a schema migration.
    """

    versions: EngineVersions = DEFAULT_VERSIONS

    def process_observation(
        self,
        conn: sqlite3.Connection,
        source_entity_type: str,
        source_observation_id: int,
    ) -> ProcessResult:
        if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
            raise RuntimeError("Normalization requires PRAGMA foreign_keys=ON")
        own_transaction = not conn.in_transaction
        savepoint = "normalization_observation"
        if own_transaction:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute(f"SAVEPOINT {savepoint}")
        try:
            source = load_source_observation(
                conn, source_entity_type, source_observation_id,
            )
            result = persist_observation_result(conn, source, versions=self.versions)
            if own_transaction:
                conn.commit()
            else:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except Exception:
            if own_transaction:
                conn.rollback()
            else:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def process_all(
        self,
        conn: sqlite3.Connection,
        *,
        source_entity_types: Sequence[str] = ("marketplace_listing", "facebook_ad"),
        batch_size: int = 200,
        max_observations: int | None = None,
    ) -> Iterator[ProcessResult]:
        """Stream source observations in bounded ID batches; no full-table load."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        processed = 0
        for entity_type in source_entity_types:
            if entity_type == "marketplace_listing":
                observation_table = "marketplace_listing_observations"
            elif entity_type == "facebook_ad":
                observation_table = "facebook_ad_observations"
            else:
                raise ValueError(f"Unsupported source entity type: {entity_type}")
            last_id: int | None = None
            while True:
                if last_id is None:
                    sql = (
                        f"SELECT id FROM {observation_table} ORDER BY id LIMIT ?"
                    )
                    rows = conn.execute(sql, (batch_size,)).fetchall()
                else:
                    sql = (
                        f"SELECT id FROM {observation_table} WHERE id > ? "
                        "ORDER BY id LIMIT ?"
                    )
                    rows = conn.execute(sql, (last_id, batch_size)).fetchall()
                if not rows:
                    break
                observation_ids = [int(row[0]) for row in rows]
                for observation_id in observation_ids:
                    if max_observations is not None and processed >= max_observations:
                        return
                    result = self.process_observation(conn, entity_type, observation_id)
                    processed += 1
                    last_id = observation_id
                    yield result
                if len(observation_ids) < batch_size:
                    break
