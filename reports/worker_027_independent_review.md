# Worker 027 — Independent Review of Migration Safety Remediation

**Review date:** 2026-10-13  
**Scope:** Independent review of Worker 026's code, tests, report, and production read-only state.  
**Production migration:** Not performed.

## Review summary

The migration and recovery command paths bind the protected database identity independently of `KRATZWERK_DB_PATH`. The focused negative tests pass, and a Windows directory-junction alias was independently tested in a temporary directory. The public `backup_database()` helper remains outside that guard and can create a backup of an arbitrary database when called directly.

The strict Foundation baseline is **not** fully closed. Its SQL canonicalizer case-folds string literals, so a semantically changed CHECK constraint is treated as an exact match. It also ignores unexpected schema objects; an extra trigger that blocks writes is accepted and can be recorded as `BASELINED`.

The currency trust boundary is **not** closed. Normal extractors and the review helper reject unsupported currency codes, but the public Facebook repository writer accepts a caller-constructed `LandingPageExtraction` without revalidation. The trusted view trusts any non-null currency paired with a trusted basis. An isolated reproduction made `XYZ` appear in `trusted_price_evidence` through the repository API.

The requested focused and full test commands both pass, but neither covers these bypasses. Production remained read-only and matched the supplied SHA-256 before and after testing.

## Findings

### F-1 — Unsupported currency can enter the trusted-price view

**Severity: High. Audit C. Evidence: source inspection and isolated execution.**

The parsers in `price_evidence/currency.py` limit recognized codes to `DZD`, `EUR`, and `USD`. The normal Ouedkniss and Facebook extraction paths preserve unrecognized source values as evidence and leave their currency basis `UNKNOWN`. `append_price_evidence_review()` also downgrades an unrecognized review currency to `UNKNOWN` and records the raw value (`price_evidence/repository.py:404`). These paths behave as intended.

The repository boundary is bypassable. `append_facebook_price_evidence()` persists the supplied candidate fields without validating `currency_code` or `currency_basis` (`price_evidence/repository.py:324`). The Migration 002 tables have no currency allowlist CHECK, and `trusted_price_evidence` checks only that the effective currency is non-null and its basis is `SOURCE_CONFIRMED` or `INDEPENDENTLY_VERIFIED` (`migrations/002_price_evidence.sql:46`, `:109`, `:222`).

**Isolated reproduction:** On a temporary database, I passed `append_facebook_price_evidence()` a caller-constructed candidate with `currency_code='XYZ'`, `currency_basis='INDEPENDENTLY_VERIFIED'`, a valid point amount, and verified product identity. I then appended an approved review without overriding currency. The view returned `(XYZ, INDEPENDENTLY_VERIFIED)`. No production database was involved.

The existing test that calls `append_price_evidence_review(currency_code='XYZ')` passes because that helper downgrades the review. It does not test forged extraction input or a database-level allowlist. Thus unknown currency is safe through the ordinary parser and review path, but a caller of the public repository API can bypass that validation.

The view correctly excludes unreviewed candidates, unknown-basis currencies, and unresolved units in ordinary stored records. The tested unsupported-currency bypass used an otherwise eligible point price and an approved review; it demonstrates that the current view's currency filter does not enforce the documented supported-code set.

### F-2 — Foundation schema comparison can baseline meaningful drift

**Severity: High. Audit B. Evidence: source inspection and isolated execution.**

The validator generates a reference schema by executing Migration 001 in memory. It checks required tables and objects, table SQL, `table_xinfo`, foreign keys, and index layouts, including automatic indexes. The expected explicit indexes and triggers include `ux_facebook_ads_url`. The seven existing negative fixtures cover missing or altered definitions and reject those cases without leaving a registry or backup.

However, `_canonical_sql()` collapses whitespace and calls `casefold()` on the entire SQL string (`schema_validation.py:115`). This also case-folds quoted string literals, which can alter CHECK semantics. In an isolated migrated database, changing the `marketplace_listings.status` CHECK literal from `'deleted'` to `'DELETED'` produced no validation problems and `inspect_database()` reported `APPLIED`. An insert using the formerly valid lowercase value `deleted` then failed the actual CHECK. Running the baseline path recorded the drifted schema as `BASELINED` with `applied_at = NULL`.

The validator also verifies a required subset rather than rejecting unrecognized objects. In a second isolated fixture I added an `unexpected_blocker` trigger on `marketplace_listings` that aborts every insert. `foundation_schema_problems()` returned no problems, and the untracked schema was recorded as `BASELINED`. That is not an exact Migration 001 schema.

These cases show that the reference comparison detects many missing constraints and objects, but it can hide meaningful differences and accept extra behavioral objects. Therefore the strict-baselining finding remains open despite passing the intended negative fixtures. The rollback implementation itself is disabled, so it does not drop these unexpected objects.

The valid untracked Foundation fixture does baseline with `applied_at = NULL`, as intended. Migration 001 renames the original legacy tables, keeps them as `legacy_*`, and the lifecycle compares source-row fingerprints across application. The tests and production read-only audit confirm that the retained tables remain present.

### F-3 — Direct backup helper bypasses target policy

**Severity: Moderate. Audit A. Evidence: source inspection and isolated execution.**

`migrate.main()`, `migrate.apply()`, `apply_migrations()`, rollback, restore, and the recovery CLI check the selected path before mutation-capable work. The CLI wrong-target test checks status, preflight, apply, rollback, and restore against a valid temporary database and verifies its bytes, schema objects, and backup set remain unchanged.

`backup_database()` is separately exported in `migration_lifecycle.__all__`, but it does not call `enforce_database_target()` (`migration_lifecycle.py:151`). A direct call with a valid temporary database and no mode argument created a `.pre-migration-v001...bak` file. The regular migration runner guards before calling this helper, so the supported CLI path remains guarded; callers using the exported helper directly do not get the promised reject-before-backup behavior.

The target normalization itself performed well. `resolve_database_path()` uses `Path.resolve(strict=False)`, and `same_database_file()` compares normalized paths plus existing file identity (`database_safety.py:20`). The focused tests executed relative paths, Windows case variants, symlink aliases, environment override protection, and direct isolated-mode rejection. I additionally created a directory junction under the system temporary directory pointing at the repository. The database path through the junction resolved to the protected path, `samefile()` returned true, and isolated mode refused it. The junction was removed afterward.

For nonexistent targets, source inspection shows resolution happens before the guard; a non-production nonexistent path can pass the identity check in isolated mode. Migration status/preflight/apply then reach `inspect_database()`, which requires the target file before backup or writes; restore checks target existence before replacement. This behavior was inspected, not separately executed. No parent or final-component alias bypass was found in the tested Windows cases.

Recovery CLI checks the protected constant independently of configuration and rejects that target before opening a write-capable connection (`recover_ouedkniss_price_evidence.py:25`). Its test patched `KRATZWERK_DB_PATH` and still observed production-target refusal.

### F-4 — Migration 001 contains a legacy DZD assumption

**Severity: Moderate, future-data risk. Audit C. Evidence: source inspection and production read-only query.**

Migration 001 writes the literal `DZD` for every migrated marketplace observation (`migrations/001_foundation_v2.sql:178`). The legacy source schema has no currency column, so a positive legacy `current_price` would receive a currency not established by that source. Historical recovery itself does not infer a price or currency from mutable listing data; it uses the exact immutable observation payload and leaves missing source evidence missing.

On the current production database, all 17 legacy listing prices are zero, all 17 corresponding migrated observation prices are `NULL`, and those rows carry `DZD`. Therefore I found no currently populated legacy price amount paired with the assumption. The SQL behavior would still assign DZD if the migration were applied to a source database with positive legacy prices, which conflicts with the no-inferred-currency rule.

## Audit D — Rollback, restore, and concurrency

`rollback_foundation()` checks the database state read-only and then refuses; it does not call backup or issue DDL (`migration_lifecycle.py:744`). The focused test added an unexpected application table, called rollback, and verified database bytes and backup paths were unchanged. After Migration 002, rollback also refuses and directs operators to restore a verified backup. No destructive Foundation rollback path was found.

Restore stages a copy of the selected backup, verifies it, preserves the prior database, moves existing `-wal`/`-shm` files beside that preserved database, and atomically replaces the target (`migration_lifecycle.py:759`). The README explicitly requires all writers and connections to be stopped and calls out WAL checkpointing (`README.md:97`). The code cannot reliably prove that no writer exists; its comment says the rename fails on Windows if a process still has the database open. This is an operational prerequisite, not an enforced precondition.

`verify_backup()` checks SQLite integrity and foreign keys, but `restore_verified_backup()` calls it without expected source fingerprints or a migration/version identity (`migration_lifecycle.py:186`, `:769`). A healthy but unrelated SQLite file can therefore pass the structural check if supplied as `--backup`. The previous target is preserved, but the code does not establish that the chosen backup is the intended pre-migration backup. Restore was exercised only by the isolated lifecycle tests; no production restore was attempted.

## Audit E — Test evidence

Before running tests, I inspected setup and teardown. The focused migration, CLI, price-evidence, and sensor tests create temporary databases and clean their temporary directories. The full suite's normalization benchmark opens production with `mode=ro`, makes a SQLite backup to a temporary benchmark file, and does not pass results/report output paths. It does not write the repository's manifests or reports. No dependencies were installed.

Commands run exactly as requested:

| Command | Exit | Tests | `unittest` time | Measured wall time |
|---|---:|---:|---:|---:|
| `\.venv\Scripts\python.exe -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q` | 0 | 71 | 9.386 s | 9.907 s |
| `\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` | 0 | 197 | 53.879 s | 54.496 s |

The pass results establish that the submitted tests run successfully; they do not close F-1 or F-2 because the bypass cases above are not in those tests. `reports/worker_026_results.json` was not present.

## Production read-only audit

I queried the protected database before and after test execution using read-only SQLite connections. The protected and configured paths both resolved to `C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\Database.sqlite`; `KRATZWERK_DB_PATH` was unset. The expected and observed SHA-256 before and after was `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`.

After testing, `PRAGMA integrity_check` returned `ok`; `PRAGMA foreign_key_check` returned zero violations; Foundation 001 was `APPLIED` with no problems; Migration 002 was `ABSENT`; `schema_migrations` was absent; and `user_version` remained `0`. Counts matched the requested baseline: `sellers=3`, `marketplace_listings=17`, `marketplace_listing_observations=51`, `facebook_ads=162`, `facebook_ad_observations=217`, `products=0`, `product_aliases=0`, `product_mappings=0`, `legacy_products=18`, `legacy_sellers=3`, `legacy_marketplace_listings=17`, `legacy_price_history=0`, and `legacy_advertising_campaigns=69`. Neither production WAL nor SHM sidecar existed at final inspection.

No production migration, capture, recovery, restore, rollback, or write-capable production connection was used. The original legacy tables and production file were not modified.

## Verdict

**NO-GO**

The target identity checks in the migration and recovery command paths are strong, but the exported backup helper is unguarded. More decisively, Foundation baselining can accept semantically drifted schemas, and unsupported currency can reach the trusted-price view through the public repository API. These findings must be resolved before the implementation is ready for a separate production-migration approval decision. This review does not authorize or execute a migration.
