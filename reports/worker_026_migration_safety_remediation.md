# Worker 026 — Migration Safety Remediation

## Result

Worker 025's two NO-GO findings are remediated in code and covered by negative
regression tests. The migration CLI now binds its default mode to an
independently defined production database identity. Foundation V2 baselining
now compares the complete migration-defined schema against an isolated
reference schema generated from Migration 001.

Foundation rollback now fails closed. Currency parsing and review writes accept
only the explicitly recognized `DZD`, `EUR`, and `USD` codes as trusted
currency evidence. Raw currency evidence is retained when the code is unknown.

## Changes

- Added `database_safety.py`. The protected target is derived from the project
  location and does not use `KRATZWERK_DB_PATH`. Path resolution handles
  relative paths, Windows case variants, symlinks/junction resolution, and
  existing file aliases.
- Migration CLI commands require the canonical production target by default.
  An explicit `--isolated-db` switch enables non-production operation and
  always refuses the protected target. The target check occurs before
  preflight, backup creation, registry writes, or database changes.
- Historical recovery independently blocks the protected target, even when
  `KRATZWERK_DB_PATH` points elsewhere, and continues to block the configured
  database.
- `foundation_schema_problems` runs Migration 001 in an in-memory reference
  database and compares the declared table SQL, columns, foreign keys,
  explicit and automatic index layouts, and explicit index/trigger SQL. The
  required `ux_facebook_ads_url` index is now included. Incomplete schemas
  cannot be recorded as `BASELINED`; an exact untracked Foundation schema keeps
  `applied_at = NULL`.
- Foundation rollback is disabled until an independently verified destructive
  procedure is available. Migration 002 continues to require restoring a
  verified backup after writers have stopped.
- Currency extractors and review writes use a dependency-free allowlist of
  `DZD`, `EUR`, and `USD`. Unknown codes remain unconfirmed with a review reason
  and raw evidence; they cannot enter the trusted-price view through the
  repository review API. `1 UNIT` remains unresolved and untrusted.
- Updated `README.md` with the target policy and operator-facing errors.

## Validation

Focused command:

```text
.\.venv\Scripts\python.exe -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q
```

Exit code **0**; **71 tests** passed in **8.794 seconds**. This includes wrong-target
pre-write failures, relative/case/symlink identity checks, explicit isolated
operation, recovery environment-override protection, seven independently
corrupted Foundation schema fixtures, currency validation, migration failure
injection and retry, and capture fail-closed coverage.

Full command:

```text
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

Exit code **0**; **197 tests** passed. `unittest` reported **52.527 seconds**;
measured wall time was **53.052 seconds**. The suite's existing normalization
benchmark opened production read-only and used a temporary benchmark copy.
SHA-256 comparisons before and after the run confirmed that all existing files
under `normalization/` and `reports/` were unchanged before this new report was
added. Isolated migration, failure/retry, restore, and historical-recovery tests
verified source row counts/fingerprints remained unchanged. Capture remained
fail-closed when Migration 002 was missing or incomplete. No dependencies were
installed.

## Production read-only audit

The configured database and protected target both resolved to:

`C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\Database.sqlite`

The database was inspected with a read-only SQLite connection and `query_only`
enabled. The supplied expected SHA-256 and both observed hashes were:

`5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`

Read-only inspection reported `integrity_check = ok`, zero foreign-key
violations, complete Foundation 001 with no validation problems, Migration 002
absent, no `schema_migrations` registry, and `user_version = 0`. The file hash
was unchanged during the audit and matches the initial baseline check.

No production migration, capture, historical recovery, restore, or rollback
was run. The original legacy database was not modified.

## Remaining untested cases

- A Windows directory junction was not created as a separate test fixture.
  Existing symlink, relative path, case-variant, and same-file checks passed;
  `Path.resolve()` is used to resolve junctions.
- No production write path was exercised, by design. The Foundation rollback
  path is verified to refuse without creating a backup or dropping existing
  and unexpected objects.
