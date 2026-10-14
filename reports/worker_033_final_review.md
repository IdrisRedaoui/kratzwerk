# Worker 033 — Final Bounded Independent Review of Migration 004

**Review date:** 2026-10-14 (Asia/Riyadh)
**Reviewed:** Migration 004, lifecycle/checksum integration, and its trust-boundary regressions
**Verdict:** **PASS — CLOSE THIS REVIEW**
**Production migration authorized or performed:** No

## Decision

The two Worker 031 attacks do not produce trusted price evidence through the
supported repository interfaces. Migration 004 closes approval and fetch
attestations by default, and its trusted-price view requires both verified
review authorization and, for Facebook evidence, verified fetch provenance.
The application has no implementation that can write those verified states.

The migration lifecycle tests confirm statement-level rollback, retry,
incomplete-state refusal, and checksum-drift refusal. Migrations 001–003 match
their Worker 031 baseline byte-for-byte. The production database remained
unchanged during this review and passes the read-only integrity checks.

This is a closure review of the current fail-closed state. It is not production
migration approval or evidence that the missing trusted services exist.

## Attack review

### Worker 031 caller-forged fetch

`append_landing_page_fetch()` accepts content for audit but always stores
`provenance_status='UNVERIFIED'`, a fixed untrusted fetcher label, a failed
normalized fetch status, and null resolved URL, HTTP status, and content type.
Caller claims remain in audit metadata. A local content hash checks byte
integrity only. `append_facebook_price_evidence()` re-extracts the stored bytes
and clears currency, unit, and product identity trust while provenance remains
unverified. The Migration 004 view requires `provenance_status='VERIFIED'` and
successful fetch metadata, so these records cannot become trusted prices.

The focused regression reproduced the Worker 031 fabricated HTML, attacker
resolved URL, claimed trusted fetcher, and `tls_verified`/`network_io` metadata.
The amount remained audit evidence, normalized claims stayed untrusted, forged
candidate currency and identity were ignored, and the trusted view returned no
row. An additional isolated direct-SQL attempt to insert a fetch already marked
`VERIFIED` was rejected by the Migration 004 trigger.

### Worker 031 spoofed UDF and unsigned approval

Migration 004 replaces the UDF-dependent trigger. Registering
`kratzwerk_price_review_authorized(...)=1` no longer authorizes an approval;
the trigger rejects every `APPROVED` insert. It also rejects any insert that
claims `authorization_status='VERIFIED'`. Existing review rows default to
`UNVERIFIED`, and review rows remain append-only.

The focused regression registered the spoofed UDF, attempted an unsigned
approval, observed the expected trigger error, confirmed the UDF was not called,
and confirmed the trusted view remained empty. The repository API separately
refuses `APPROVED`; it has no signature or authorization-status input. Attempts
to pass a signature are rejected as unsupported arguments, and missing signer
configuration does not enable approval. An additional isolated direct-SQL
attempt to insert a nonapproval review with forged `authorization_status='VERIFIED'`
was rejected by the trigger.

## Migration lifecycle and compatibility

Migration 004 is registered as version 4 in `migration_lifecycle.MIGRATIONS`.
The lifecycle computes the SHA-256 from the migration file, checks recorded
filename/checksum/schema state before application, and validates Migration 004
object definitions during preflight and postflight. Application runs its seven
SQL statements in one transaction; it records the applied version only after
schema, source fingerprints, integrity, and foreign-key checks pass. Exceptions
roll back the active transaction. Migration 004 is forward-only; recovery uses
the existing verified-backup lifecycle.

The focused lifecycle test injected a failure after each of the seven SQL
statements. Every failure left Migration 003 applied, Migration 004 absent, and
source fingerprints unchanged. A subsequent retry applied Migration 004 and
preserved the Migration 003 checksum. Separate cases rejected partial Migration
004 state and a modified recorded Migration 004 checksum. The review did not
run a destructive rollback or restore operation.

Independent SHA-256 values:

| Migration | SHA-256 | Comparison |
|---|---|---|
| 001 `001_foundation_v2.sql` | `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` | Matches Worker 031 baseline |
| 002 `002_price_evidence.sql` | `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844` | Matches Worker 031 baseline |
| 003 `003_price_evidence_trust_policy.sql` | `f91fb42af6d2f9556fafe6252b7395c85bb3e1778abc11195f5f1fa5a51245b6` | Matches Worker 031 baseline |
| 004 `004_closed_price_trust_boundaries.sql` | `f8677b4c76dd956f4316a12d4ec09d4b9161f54f06aa10201fa32a4e9eda03f3` | Registered version-4 migration checksum |

`git diff --exit-code 6324cc8 HEAD -- migrations/001_foundation_v2.sql migrations/002_price_evidence.sql migrations/003_price_evidence_trust_policy.sql`
returned exit code 0. No migration SQL was changed during this review.

## Commands and results

All tests used disposable databases. `-B` prevented Python bytecode writes.

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -B -m unittest tests.test_price_evidence.OuedknissPriceEvidenceTests.test_worker_031_fabricated_fetch_bytes_and_claims_stay_untrusted tests.test_price_evidence.OuedknissPriceEvidenceTests.test_worker_031_spoofed_sqlite_udf_cannot_authorize_unsigned_approval tests.test_migration_lifecycle.MigrationLifecycleTests.test_migration_004_failure_injection_retry_and_checksum_preservation tests.test_migration_lifecycle.MigrationLifecycleTests.test_migration_004_partial_state_and_checksum_drift_are_rejected -q` | Exit 0; 4 tests passed, including rollback at every Migration 004 statement, successful retry, incomplete-state refusal, and checksum-drift refusal. |
| `.\.venv\Scripts\python.exe -B -m unittest tests.test_price_evidence.OuedknissPriceEvidenceTests.test_unavailable_approval_leaves_price_evidence_untrusted_and_unchanged tests.test_price_evidence.OuedknissPriceEvidenceTests.test_signature_attempts_missing_key_and_nonstandard_review_json_fail_closed -q` | Exit 0; 2 tests passed. |
| Disposable direct-SQL probe for `provenance_status='VERIFIED'` and `authorization_status='VERIFIED'`, plus repository `APPROVED` call | Both forged verified-state inserts were rejected; repository approval was rejected; trusted view contained 0 rows. |
| `git diff --exit-code 6324cc8 HEAD -- migrations/001_foundation_v2.sql migrations/002_price_evidence.sql migrations/003_price_evidence_trust_policy.sql` | Exit 0; Migrations 001–003 unchanged from the Worker 031 baseline commit. |
| SHA-256 of migration files and statement count via `migration_lifecycle.migration_checksum()` and `_sql_statements()` | Checksums as listed above; Migration 004 has 7 SQL statements. |

The full test suite was not run. The focused tests directly cover the scoped
attack paths and Migration 004 failure cases; no cross-component regression
was indicated that required broader testing.

## Production read-only audit

Before and after values were collected from `data/Database.sqlite` using a
SQLite `mode=ro` URI with `PRAGMA query_only=ON`; SHA-256 was computed from file
bytes. No write-capable production connection was opened.

| Check | Before | After | Result |
|---|---|---|---|
| SHA-256 | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | Unchanged; matches Worker 031 baseline |
| `PRAGMA integrity_check` | `ok` | `ok` | Pass |
| `PRAGMA foreign_key_check` | 0 violations | 0 violations | Pass |
| `PRAGMA user_version` | 0 | 0 | Unchanged |
| `schema_migrations` | Absent | Absent | Unchanged |
| Migration 002/003/004 price-evidence objects | Absent | Absent | Unchanged |
| `-wal` / `-shm` sidecars | Absent / absent | Absent / absent | Unchanged |

Production table counts were unchanged: `sellers` 3; `marketplace_listings` 17;
`marketplace_listing_observations` 51; `facebook_ads` 162;
`facebook_ad_observations` 217; `products` 0; `product_aliases` 0;
`product_mappings` 0; `legacy_products` 18; `legacy_sellers` 3;
`legacy_marketplace_listings` 17; `legacy_price_history` 0; and
`legacy_advertising_campaigns` 69. SQLite's internal `sqlite_sequence` had 4
rows both before and after.

No production migration, capture, approval, backup, recovery, restore, rollback,
or write-capable operation was performed. No production data or review key was
modified or created.

## Remaining conditions and recommendation

The missing services are intentionally disabled and fail closed. A future,
separately scoped and independently reviewed implementation is required before
any trusted price can appear:

- A controlled fetch service must bind a fetch to the exact ad observation and
  destination, validate DNS and every redirect/target, and authenticate its
  final URL, status, content type, and fetch time.
- An authenticated reviewer identity boundary, isolated signer, strict
  signed-statement verification, key IDs and lifecycle, and controlled verified
  persistence path must be implemented.
- Filesystem and process access must protect the SQLite file from unrestricted
  direct writers. SQLite affinity cannot distinguish a Python `True` from
  numeric `1` after coercion, and database triggers do not defend against a
  writer able to alter the schema or data directly.

Until those services are separately authorized and reviewed, keep trusted price
approval disabled and treat the empty `trusted_price_evidence` view as the
expected state. **Close the current trust-boundary review and move project
effort to deterministic Normalisation and offline AI evaluation.**
