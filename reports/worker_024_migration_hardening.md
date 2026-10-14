# Worker 024 — Migration Hardening, Capture Safety Gate & Historical Price Evidence Recovery

Run date: 2026-10-13 (Asia/Riyadh)

## Result

Worker 023’s migration and capture-safety findings are addressed in code and validated on isolated SQLite copies. Migration 002 remains unapplied to the configured production database, and no live capture or external page fetch was run.

The configured database has the complete Foundation V2 schema and its retained legacy tables, so Migration 001 is structurally present. It has no migration registry and `PRAGMA user_version` is `0`; its original application timestamp cannot be recovered. The runner records such verified history as `BASELINED` with `applied_at = NULL`, rather than inventing an application time. The production database was not changed to add this registry.

## Migration lifecycle

[`migrate.py`](../migrate.py) now provides `status`, `preflight`, `apply`, `rollback-001`, and `restore` commands. [`migration_lifecycle.py`](../migration_lifecycle.py) maintains `schema_migrations` with version, filename, SHA-256 checksum, applied timestamp, registry timestamp, state, and verification details.

Each numbered migration runs in its own `BEGIN IMMEDIATE` transaction. The runner enables SQLite foreign keys before beginning the transaction, executes DDL statement by statement, validates the expected schema and unchanged source-row/ID fingerprints, checks `integrity_check` and `foreign_key_check`, then writes the applied registry row as the final transactional change. A recorded checksum mismatch is rejected. Migration 001 and 002 checksums are respectively `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` and `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844`.

Before each migration, the runner makes a SQLite online backup in a separate file. It independently opens the backup, checks its hash, integrity, foreign keys, source counts, and source-row/ID fingerprints. Once it has the write lock, it compares the current source fingerprints with the backup to detect changes made between backup and migration. Partial schemas and object-name collisions fail preflight without dropping or replacing anything.

The injected Migration 002 failure occurred after SQL statement 5 on an isolated database. The transaction rolled back all Migration 002 DDL and registry changes; no price-evidence tables or partial objects remained, and Migration 001 remained recorded. Retrying applied Migration 002 successfully. A deliberately partial `price_evidence` table was detected; the runner left it untouched, and restoring a verified backup returned the isolated database to a healthy pre-migration state. A deliberately changed recorded checksum was rejected. Calling the old rollback function after Migration 002 now fails with restore-from-backup guidance; a verified backup restore passed.

## Backup and restore validation

The verified pre-Migration-002 backup used for the command-line restore check is [`worker_024_cli_validation.sqlite.pre-migration-v002.20261013T104708.248094Z.bak`](../data/worker_024_cli_validation.sqlite.pre-migration-v002.20261013T104708.248094Z.bak).

- Backup SHA-256: `76daef695f0088007075b1b3878383caa2c4fc00fd4fd395edfba9ad25d32c70`
- Backup integrity check: `ok`
- Backup foreign-key violations: `0`
- Restored file SHA-256: identical to the backup
- Post-restore integrity check: `ok`
- Post-restore foreign-key violations: `0`
- Post-restore status: Migration 001 verified baseline; Migration 002 absent
- Source counts and fingerprints: unchanged

The restore stages and verifies a new file before replacement and preserves the previous database and any `-wal`/`-shm` sidecars. For on-disk restore, stop all writers and close every connection first. In WAL mode, checkpoint with `PRAGMA wal_checkpoint(TRUNCATE)` before closing; review preserved sidecars before removing them.

## Ouedkniss capture readiness

[`sensors/ouedkniss_sensor.py`](../sensors/ouedkniss_sensor.py) now performs a read-only readiness check against `config.DATABASE_PATH` before network collection and again before opening a write connection. The check requires the full Migration 002 object set, expected columns, foreign keys, exact SQL definitions, and a queryable trusted-price view. Missing or incomplete schema raises a clear Migration 002 error naming the configured path and corrective action. The sensor never applies migrations. The evidence repository also raises if the schema disappears instead of silently returning zero.

Synthetic tests verify that missing schema blocks fetch and writes, that listings and observations remain untouched, that an incomplete schema fails closed, and that valid schema permits the existing capture path. The capture tests retain `UNIT` as unresolved, currency as unknown, missing prices as missing evidence, and evidence as append-only. Read-only migration status remains available when Migration 002 is absent. No live Ouedkniss capture was run.

## Historical evidence recovery

[`price_evidence/recovery.py`](../price_evidence/recovery.py) and [`recover_ouedkniss_price_evidence.py`](../recover_ouedkniss_price_evidence.py) implement a separate, resumable utility. It reads source values and timestamps from each exact immutable observation payload, plus the exact observation’s NULL `price` only for Migration 001 snapshots carrying the explicit unknown-price marker. It does not use mutable listing price, listing ID text, or URL to reconstruct historical prices; source identity comes from the observation payload or its exact listing foreign key. Each insert uses the observation ID and extractor method/version in its stable idempotency key and commits independently.

On an isolated online-backup copy of the configured database, the first recovery pass examined 51 Ouedkniss observations and recorded 51 evidence/status rows:

This does **not** mean that 51 source prices existed. The payloads establish numeric source amounts for 16 observations; the other 35 rows represent missing-price evidence/status only.

| Result | Count | Interpretation |
| --- | ---: | --- |
| Numeric source candidates | 16 | Each source amount is `1`, source unit is `UNIT`, unit remains unresolved, and currency remains unknown |
| Suspicious repeated `1 UNIT` candidates | 16 | Flagged for review; none is confirmed DZD |
| Missing-price evidence rows | 35 | 18 source payloads have no price and 17 Migration 001 snapshots have an explicit unknown-price marker and NULL observation price |
| Trusted price rows | 0 | No candidate has an approval review |
| Skipped / failed on the first pass | 0 / 0 | Every observation had source-grounded candidate or missing-status evidence |

The immutable marketplace-observation row fingerprint remained `05ffaa36855a774226a253815b9752049c6d285d94a564a405fcb776a957d524`; the ID fingerprint remained `c331e85759e39947a7591a5b52bab252382637ab99ac844cee79867609389c68`. The rerun imported `0`, found the same 51 idempotency keys, and left the source fingerprints unchanged. Both integrity and foreign-key checks passed.

The final isolated artifact is [`worker_024_validation.sqlite`](../data/worker_024_validation.sqlite), SHA-256 `5211e5c962706dbced97eb3aec5f9ce10f453e1904ff3e9b02622593293bb56d`. It contains Migration 002 and the 51 derived evidence/status rows; its original observations are unchanged. A separate CLI rehearsal copy and verified pre-migration backup are also retained in `data/`.

## Commands

These commands were exercised only against the explicitly selected isolated copies. `worker_024_cli_validation.sqlite` was preflighted, migrated, status-checked, then restored from its verified backup.

```powershell
& .\.venv\Scripts\python.exe migrate.py status --db data/worker_024_cli_validation.sqlite
& .\.venv\Scripts\python.exe migrate.py preflight --db data/worker_024_cli_validation.sqlite --target 2
& .\.venv\Scripts\python.exe migrate.py apply --db data/worker_024_cli_validation.sqlite --target 2
& .\.venv\Scripts\python.exe migrate.py status --db data/worker_024_cli_validation.sqlite
& .\.venv\Scripts\python.exe migrate.py restore --db data/worker_024_cli_validation.sqlite --backup data/worker_024_cli_validation.sqlite.pre-migration-v002.20261013T104708.248094Z.bak
& .\.venv\Scripts\python.exe recover_ouedkniss_price_evidence.py --db data/worker_024_validation.sqlite
```

The final recovery command was run against an already recovered isolated copy and therefore reported zero new inserts. To reproduce its first pass, start with a separate isolated backup copy, apply Migration 002 to that copy, then invoke the utility. Do not point recovery or migration commands at `data/Database.sqlite`.

## Verification and safety

Full suite command: `\.venv\Scripts\python.exe -m unittest discover -s tests -q`

- Tests: `187`
- Exit code: `0`
- Elapsed time: `64.606` seconds
- Normalization benchmark regression tests passed; normalization and matching code were not changed.
- Worker 018 manifest SHA-256 remained `7ca5adbb43e1eac270d58d03b4ca8292b713d61b125595b039f114179b17e686`.
- Production database SHA-256 before and after: `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`.
- Production integrity: `ok`; foreign-key violations: `0`; Migration 002 table and registry remain absent.
- No production database modification, production capture, live Ouedkniss request, Facebook landing-page fetch, provider/API call, or dependency installation occurred.
- Worker 018/019/020/021 reports and the Worker 018 manifest were not changed.

| Production table | Before | After |
| --- | ---: | ---: |
| sellers | 3 | 3 |
| marketplace_listings | 17 | 17 |
| marketplace_listing_observations | 51 | 51 |
| facebook_ads | 162 | 162 |
| facebook_ad_observations | 217 | 217 |
| products | 0 | 0 |
| product_aliases | 0 | 0 |
| product_mappings | 0 | 0 |
| legacy_products | 18 | 18 |
| legacy_sellers | 3 | 3 |
| legacy_marketplace_listings | 17 | 17 |
| legacy_price_history | 0 | 0 |
| legacy_advertising_campaigns | 69 | 69 |

## Remaining deployment condition

The lifecycle, restore procedure, and capture gate passed isolated validation. Production Migration 002 and live capture remain intentionally unperformed. The production schema still has no price-evidence tables, so the new capture path will block until a separately reviewed deployment applies Migration 002 with the documented backup and verification sequence.

Recommended future production sequence, not executed in this worker: pause capture writers; confirm the configured database path and a verified external backup; run read-only preflight; apply Migration 002 through the versioned runner; verify registry/checksums, schema objects, integrity, foreign keys, and source fingerprints; then separately approve historical recovery from immutable observations. If migration verification fails, stop writers and restore the verified pre-migration backup. No production command was run.
