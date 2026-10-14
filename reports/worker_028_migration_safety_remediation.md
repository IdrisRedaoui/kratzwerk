# Worker 028 — Migration Safety Remediation

**Date:** 2026-10-13  
**Review basis:** Worker 027 independent findings  
**Verdict:** **READY FOR INDEPENDENT REVIEW**

This verdict means the remediation and evidence are ready for a fresh independent
review. It is not production migration approval. Migration 002 and Migration 003
were not applied to the protected database.

## Source inspection and changes

### Currency trust boundaries

`price_evidence/repository.py` now revalidates caller-supplied Facebook
extractions and candidates at the public write boundary, including exact model
types, currency basis, candidate evidence, and currency code. Only `DZD`, `EUR`,
and `USD` are canonicalized as supported codes. An unsupported or malformed
value is retained in supporting evidence, while the stored code becomes `NULL`
and its basis becomes `UNKNOWN`. Review writes apply the same allowlist and
downgrade unsupported input. Parser-produced values are not treated as a
substitute for repository validation.

Migration 002 remains unchanged. Forward-only Migration 003 adds database
insert triggers for the allowlist and replaces `trusted_price_evidence` with a
view that independently requires an allowlisted currency, eligible basis,
approved review, acceptable unit and point price type, valid quality, and
verified product identity. A malformed unsupported row injected by a lower-
level fixture is also excluded by the view. No supported code can be inferred
from a missing code or forged basis.

### Foundation schema comparison

`schema_validation.py` tokenizes SQL, ignores formatting/comments, applies
SQLite-compatible ASCII case normalization to identifiers and keywords, and
preserves string literal contents exactly. Quoted identifier delimiters are
normalized while identifier spelling is handled using ASCII-only
case-insensitivity. Table shape checks include `table_xinfo`, foreign keys,
explicit and automatic index layouts, and index metadata. Unexpected indexes
or triggers attached to Foundation-managed tables fail validation. Existing
legacy tables retained by Migration 001 remain part of the expected state and
are not required to disappear or have their rows rewritten.

The expected schema is generated in memory from Migration 001. Incomplete or
drifted schemas fail before backup creation or baseline registry writes. A
valid untracked Foundation schema can still be recorded as `BASELINED` with
`applied_at = NULL` when its original application time is unknown.

### Migration 001 currency compatibility

Migration 001 is immutable because it may already be checksum-recorded. Its
historical observation rows still contain the literal `DZD`, and positive
legacy amounts remain preserved. Migration 003 adds
`legacy_currency_unconfirmed_observations`, which exposes Migration 001 amounts
as `raw_legacy_amount`, with `currency_code = NULL` and `currency_basis =
'UNKNOWN'`, retaining the original observation payload. The trusted-price view
excludes evidence linked to those rows unless a later approved review records
an independently verified supported currency. Recovery reads the exact
immutable observation payload and does not reconstruct a price from a mutable
listing row.

The supported upgrade sequence is Migration 001, Migration 002, then Migration
003. Migration 001 and 002 files/checksums were left unchanged; Migration 003
has its own registry version and checksum. Existing databases with recorded 001
and 002 checksums can advance using the versioned runner. Capture and recovery
require complete Migration 002 and Migration 003 schemas and fail closed if
either is absent or incomplete.

### Backup and restore policy

The exported `backup_database()` helper now enforces the canonical target
policy itself before file checks or backup creation. Non-production callers
must explicitly pass `isolated=True`; the protected production path is refused
even in that mode. The migration, rollback, and restore public mutation helpers
also enforce the target policy. Read-only inspection and verification helpers
do not mutate database files.

Each backup receives a signed manifest binding a unique backup ID, backup file
name and SHA-256, canonical intended target identity, migration purpose,
creation time, and verified schema/migration state. Restore requires a valid
manifest for the selected target, validates the backup hash and schema state,
stages and rechecks the candidate before replacement, and preserves the prior
database and its WAL/SHM sidecars. It also requires the explicit
`writers_stopped=True` / `--writers-stopped` acknowledgment. That acknowledgment
is an operational precondition; the helper cannot prove that every writer has
stopped.

The manifest HMAC uses a per-user key in the user's application-data directory,
outside the backup folder. Keep that key available with the backup. If the key
is unavailable, the automated restore refuses the backup. For backups with
missing or unverifiable provenance, the documented recovery path is an offline
manual recovery reviewed against the intended target and source fingerprints;
SQLite integrity alone is not accepted as provenance.

### Files changed

- `README.md`
- `SENSOR_V2_INTEGRATION.md`
- `migrate.py`
- `migration_lifecycle.py`
- `migrations/003_price_evidence_trust_policy.sql` (new)
- `price_evidence/recovery.py`
- `price_evidence/repository.py`
- `schema_validation.py`
- `sensors/ouedkniss_sensor.py`
- `tests/test_foundation_v2.py`
- `tests/test_migration_lifecycle.py`
- `tests/test_price_evidence.py`
- `reports/worker_028_migration_safety_remediation.md` (this report)
- `reports/worker_028_results.json`

Migration 001, Migration 002, the protected database, the original legacy
database, existing normalization manifests, and preexisting reports were not
edited. No dependencies were installed.

## Executed tests

Test setup and teardown were inspected. Migration, restore, failure-injection,
schema-corruption, and currency-forgery fixtures use temporary databases. The
full suite's Worker 018 benchmark opens the protected database with a read-only
SQLite URI, makes a temporary copy, and applies benchmark writes only to that
copy.

| Command | Exit | Tests | `unittest` duration |
|---|---:|---:|---:|
| `\.venv\Scripts\python.exe -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q` | 0 | 85 | 31.150 s |
| `\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` | 0 | 211 | 91.901 s |

Both commands passed on the final code state. The full suite includes expected
failure-injection log messages from tests asserting transaction rollback;
there were no test failures.

Regression evidence includes:

- The Worker 027 forged Facebook `XYZ` / `INDEPENDENTLY_VERIFIED` candidate
  followed by an approved review no longer appears in `trusted_price_evidence`.
- Positive write-path tests cover `DZD`, `EUR`, and `USD`; malformed and
  unsupported values including `XYZ` remain unconfirmed with raw evidence.
- Database triggers reject unsupported inserted codes. A lower-level malformed
  fixture still cannot make the trusted view emit an unsupported currency.
- The exact `'deleted'` to `'DELETED'` CHECK-literal mutation is rejected
  before baseline registry or backup creation. SQL formatting and quoted
  identifier variants normalize without folding string literals or
  non-ASCII identifier semantics.
- The unexpected insert-blocking trigger is rejected before baseline registry
  or backup creation. Existing negative cases for missing indexes, foreign
  keys, UNIQUE/CHECK constraints, altered table/index definitions, and missing
  required triggers remain in the suite.
- Direct wrong-target `backup_database()` calls, environment overrides, and
  path aliases are rejected before a backup file or database change.
- Restore tests cover a valid manifest-bound restore, unrelated healthy and
  unmanifested backups, altered backup bytes, mismatched manifest/target,
  absent writer acknowledgment, and preservation of old WAL/SHM sidecars.
- Migration 002/003 failure injection and retry preserve source fingerprints;
  baseline rejection leaves no registry row or backup. Capture tests reject
  absent and incomplete schemas before source writes.
- A positive legacy-price fixture preserves its source amount, exposes it as
  currency-unknown, skips unsupported historical recovery, and stays out of
  trusted prices until an independent supported-currency review is appended.

## Production read-only audit

The protected database was checked before and after the work. Both SHA-256
observations equal the required baseline:

`5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`

The final audit used SQLite `mode=ro` and `PRAGMA query_only=ON`. A separate
file hash check matched the same value. `PRAGMA integrity_check` returned
`ok`; `PRAGMA foreign_key_check` returned zero violations. Foundation 001 is
`APPLIED` with no schema problems. Migration 002 and Migration 003 are both
`ABSENT`, `schema_migrations` is absent, and `user_version` is `0`.

| Table | Count | Table | Count |
|---|---:|---|---:|
| `sellers` | 3 | `marketplace_listings` | 17 |
| `marketplace_listing_observations` | 51 | `facebook_ads` | 162 |
| `facebook_ad_observations` | 217 | `products` | 0 |
| `product_aliases` | 0 | `product_mappings` | 0 |
| `legacy_products` | 18 | `legacy_sellers` | 3 |
| `legacy_marketplace_listings` | 17 | `legacy_price_history` | 0 |
| `legacy_advertising_campaigns` | 69 | | |

The 17 observations explicitly marked as Migration 001 imports have `DZD` in
the immutable historical row but all 17 have `NULL` prices. The production
legacy listings have no positive `current_price`. Their uncertainty overlay
is tested on an isolated positive-price fixture. No WAL or SHM sidecar existed
at final audit.

The benchmark manifest and prior Worker 027 review artifacts were not modified.
The manifest's observed SHA-256 is
`7ca5adbb43e1eac270d58d03b4ca8292b713d61b125595b039f114179b17e686`;
Worker 027 results and review SHA-256 values are recorded in the machine
readable evidence file. As part of the required full suite, the benchmark
created an isolated temporary SQLite snapshot using a `mode=ro` production
connection; benchmark writes were confined to that temporary copy. No migration
backup helper or operator backup, migration, capture, recovery, restore, or
rollback targeted production, and the old legacy database was not modified.

## Reasoning and unresolved risks

The three Worker 027 direct defects now have explicit regressions and
independent enforcement at their relevant boundaries. Migration 003 closes the
legacy-currency ambiguity without editing a checksum-recorded migration or
rewriting append-only observations. The target guard, manifest verification,
and staged restore jointly bind backup provenance to the intended database.

Remaining operational risks are explicit: local loss of the manifest signing
key makes automated restore unavailable; a signed manifest is only as protected
as that local key; and `--writers-stopped` is an operator acknowledgment, not
an active-connection detector. Backups created before the manifest policy do
not gain provenance retroactively and must use the manual recovery procedure.
Migration 003 has not been installed on production and must receive a fresh
independent review before any later migration approval.

## Verdict

**READY FOR INDEPENDENT REVIEW**

This does not authorize applying Migration 002 or 003 to production. The next
decision belongs to a fresh independent reviewer.
