# Worker 029 — Independent Review of Worker 028 Migration Safety Remediation

**Review date:** 2026-10-13 (Asia/Riyadh)  
**Review basis:** Worker 028 implementation, migration files, test fixtures, lifecycle helpers, and read-only production state  
**Verdict:** **NO-GO**

The Migration 001/002 immutability, Migration 003 runner lifecycle, prior Worker 027 regressions, and production preservation checks passed. I found two reproducible currency/price trust-boundary defects: a caller can promote a currency and basis that contradict or are absent from the fetched source, and a nonnumeric text amount can appear in `trusted_price_evidence`. Both are reachable through the public repository API. Do not authorize a separate production-migration decision until they are resolved and independently reviewed.

This verdict does not authorize applying Migration 002 or 003. Neither migration was applied to production.

## Scope and safety

I inspected Worker 028’s implementation rather than relying on its report. No source code, tests, prior reports, production data, original legacy database, or normalization manifest was modified. All database mutations and restore experiments used temporary isolated databases. No dependencies were installed. Production inspection used read-only SQLite connections and `PRAGMA query_only=ON`.

The full suite’s benchmark setup was inspected before it was run. It opens production with `mode=ro`, copies data to a temporary benchmark database, and performs writes only on that copy. The migration, currency, capture, recovery, and restore fixtures use temporary databases.

## Findings

### F-01 — Caller-supplied Facebook currency and basis can be trusted without source support

**Severity: High. Disposition: NO-GO.**

`append_facebook_price_evidence()` checks model types and allowlists currency codes and basis strings, but it does not verify that the structured currency or basis agrees with `raw_value`, `raw_price_text`, or the content hash recorded for the fetch. The fetch helper permits `fetch_status='FETCHED'` with no content or content hash. The review helper accepts an arbitrary `reviewer` string and an `APPROVED` decision; it does not establish reviewer authorization.

**Isolated reproduction:** I created a Facebook fetch record with no content hash, then submitted a caller-constructed candidate whose raw price had no currency but whose structured fields said `currency_code='DZD'` and `currency_basis='INDEPENDENTLY_VERIFIED'`. I approved it through the public review helper with `reviewer='untrusted-caller'` and left the review currency fields unset. The repository stored DZD/INDEPENDENTLY_VERIFIED, and the trusted view returned the row with those values.

I also supplied raw text and JSON declaring `XYZ` while the structured candidate declared DZD/SOURCE_CONFIRMED. After an approved review, the trusted view returned DZD/SOURCE_CONFIRMED. Thus the allowlist prevents an unsupported *stored code* such as XYZ, but does not establish that an allowed code is supported by source or independent evidence.

**Expected:** Missing or contradictory source currency/basis remains unknown unless a separately authorized review records supported independent evidence.  
**Actual:** Caller-authored structured fields are accepted, and a public self-approved review makes them trusted.  
**Affected boundary:** `price_evidence/repository.py` (`append_landing_page_fetch`, `append_facebook_price_evidence`, `append_price_evidence_review`) and `trusted_price_evidence` in Migration 003.  
**Condition for reconsideration:** Establish and verify source-to-extraction provenance and an authorized review boundary. A caller-supplied model alone must not assert independent verification.

The narrow Worker 027 reproduction still passes: a caller-constructed candidate with `currency_code='XYZ'` and `INDEPENDENTLY_VERIFIED` is downgraded to NULL/UNKNOWN, and an approved review carrying XYZ does not make it trusted. The defect is that supported-but-forged fields remain accepted.

### F-02 — Nonnumeric price text can enter the trusted-price view

**Severity: Moderate. Disposition: NO-GO.**

The public Facebook repository writer does not validate the runtime type of `candidate_amount`. Migration 002 declares a REAL column with `CHECK (candidate_amount IS NULL OR candidate_amount >= 0)`, but SQLite retains a nonnumeric string in a REAL-affinity column and considers that text value greater than a numeric value for this comparison. The trusted view checks only that the amount is non-NULL and has no maximum amount.

**Isolated reproduction:** I submitted a `PriceCandidate` with `candidate_amount='not-a-number'`, valid-looking review fields, and an approved review. The row stored as `typeof(candidate_amount)='text'` and was returned by `trusted_price_evidence` with the amount still equal to `not-a-number`.

**Expected:** Reject malformed amount types before insert, or ensure the schema/view excludes nonnumeric values.  
**Actual:** A malformed amount is labeled trusted.  
**Affected boundary:** `price_evidence/repository.py:append_facebook_price_evidence`, Migration 002’s amount CHECK, and the amount predicates in the trusted-price view.  
**Condition for reconsideration:** Add runtime and database-level type enforcement, then reproduce this case and rerun the price-evidence suite.

### F-03 — Schema validation rejects equivalent identifier spellings

**Severity: Low. Disposition: Document and correct before using the validator for broad legacy baselining.**

The tokenizer correctly preserves string literals and detected all tested differences in CHECK literals, collations, defaults, foreign-key actions, UNIQUE constraints, partial and expression indexes, trigger bodies, and view definitions. Formatting, comments, and alternate quoted-identifier delimiters canonicalized as expected.

The validator is not a complete SQLite semantic normalizer. I renamed the managed `products` table through a temporary name to `PRODUCTS` in an isolated Migration 001 database. SQLite treats those ASCII identifier spellings equivalently, but the validator’s exact object-name and PRAGMA-shape comparisons reported Foundation as PARTIAL. `_canonical_sql()` also treats an unquoted `Foo` and quoted `"foo"` as different token kinds even when the name is an ordinary non-keyword identifier.

**Expected:** Accept schema definitions that are equivalent under supported SQLite identifier rules.  
**Actual:** A valid identifier-case variation is rejected before baselining.  
**Affected boundary:** `schema_validation.py` object-name lookup, table-shape comparisons, and `_sql_tokens()` canonicalization.  
**Condition for reconsideration:** Either normalize these documented equivalences consistently across `sqlite_master` and PRAGMA results, or explicitly constrain and document the accepted schema dialect for existing databases.

No false acceptance of a meaningful SQL change was reproduced in the tested cases. This is not proof of completeness: the validator is token-based, not a full SQLite parser, and its equivalence rules do not cover every possible SQLite DDL spelling or extension.

### F-04 — A staged restore verification exception leaves a database copy behind

**Severity: Moderate. Disposition: Fix before relying on restore in an operational recovery.**

I injected an exception from the staged backup’s `verify_backup(stage)` call. `restore_verified_backup()` left the target byte-for-byte unchanged, but left the full staged SQLite copy named `.target.sqlite.restore-stage-….sqlite` beside the target. Cleanup is explicit for a schema-state mismatch and for replacement failures, but not for an exception during the initial staged verification.

**Expected:** On any staging or verification failure, preserve the target and remove the temporary staged copy.  
**Actual:** Target preserved; staged copy remains.  
**Affected boundary:** `migration_lifecycle.py:restore_verified_backup`, after the stage copy and at `verify_backup(stage)`.  
**Condition for reconsideration:** Exercise exception-safe cleanup for every stage operation, including copy and verification failures.

### F-05 — Missing HMAC key is silently replaced before verification fails

**Severity: Low. Disposition: Operational limitation.**

The documented key-loss behavior fails closed, and the README tells operators to preserve the per-user key and use the manual recovery path if it is unavailable. In an isolated test with the key path missing, `_manifest_signing_key()` generated a new 32-byte key, then manifest verification failed with “signature is invalid.” The backup was not accepted or modified. This is safe against unauthorized restore, but it obscures key loss as signature corruption and creates a replacement key during a read/verify operation.

**Affected boundary:** `migration_lifecycle.py:_manifest_signing_key()` and `verify_backup()`; operational guidance in `README.md`.  
**Condition:** Recover the original key before automated restore. If it is lost, follow the documented offline/manual recovery procedure and do not treat the generated replacement key as validating old backups.

## Review A — Worker 027 findings

All five requested cases were reproduced on isolated databases at their intended boundaries:

| Case | Expected boundary | Reproduced result |
|---|---|---|
| Forged Facebook XYZ / independently verified, then approved | Repository writer and trusted view | Stored currency was NULL, basis UNKNOWN, and trusted-view count was 0. |
| CHECK literal changed from `'deleted'` to `'DELETED'` | Foundation schema validator before baseline/backup | State was PARTIAL; apply refused before registry creation or a new backup. Database bytes were unchanged. |
| Unexpected insert-blocking trigger | Foundation schema validator before baseline/backup | Trigger was reported as unexpected; apply refused before registry creation or a new backup. Database bytes were unchanged. |
| Direct `backup_database()` call on wrong target | Exported helper’s target guard | `DatabaseTargetError` occurred before creating a backup or changing the target. |
| Healthy unrelated or unmanifested restore source | Restore manifest/target verifier | A signed backup for another target and a healthy unmanifested file were rejected before replacement; target bytes were unchanged. |

The focused suite also passed the environment-override and symlink-alias target checks (`test_environment_override_does_not_change_protected_identity`, `test_relative_case_and_symlink_aliases_resolve_consistently`).

## Review B — Migration 003 lifecycle and compatibility

Migration 001 and 002 remain byte-for-byte unchanged against the earlier registry values in the read-only Worker 024 validation database:

| Migration | Recorded checksum | Current file checksum | Match |
|---|---|---|---|
| 001 | `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` | same | yes |
| 002 | `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844` | same | yes |
| 003 | not previously recorded | `0661a45a9c48fca4915049c06b1826d5b6e6229b994dd92bfb5ea819b5f8b014` | new |

I copied that existing database, which already had valid Foundation 001, Migration 002, and registry rows 1 and 2, then applied Migration 003 through `apply_migrations(..., target_version=3, isolated=True)`. The result was 001 → 002 → 003 APPLIED, with registry versions `[1, 2, 3]`, unchanged 001/002 checksums, and unchanged source-observation fingerprints. All 19 required price-evidence objects were present: three tables, five indexes, nine triggers, and the trusted-price and legacy-currency-uncertainty views.

Failure injection after each of Migration 003’s five DDL statements rolled back the schema and registry to versions 1 and 2, preserved source fingerprints, and allowed a successful retry. I also confirmed rejection of checksum drift, incomplete Migration 002 schema, and a conflicting Migration 003 object name. Reapplying an already-applied 003 produced no backup and left the registry stable.

For a valid but untracked existing 001+002 schema, the runner recorded migrations 1 and 2 as BASELINED with `applied_at = NULL`; only newly applied Migration 003 received an application timestamp. No earlier application time was invented.

Capture (`process_and_store_data`) and recovery failed closed, without changing database bytes, against Foundation-only, Migration 002 absent, Migration 002 incomplete, and Migration 003 incomplete isolated schemas.

The positive legacy-price fixture preserved amount `72000` in the Migration 001 observation, projected it through `legacy_currency_unconfirmed_observations` as `raw_legacy_amount=72000`, `currency_code=NULL`, and `currency_basis='UNKNOWN'`, and did not expose it as trusted without an independent supported-currency review.

## Review C — Currency trust boundary

The exact Worker 027 unsupported-code path now works: malformed/unsupported raw currency is retained in supporting evidence, structured currency is downgraded to NULL/UNKNOWN, insert triggers reject unsupported non-NULL codes, and the trusted view’s allowlist excludes unsupported currencies even when lower-level rows are forged after removing the test triggers.

The broader boundary does not hold. The public Facebook repository API accepts caller-constructed supported currency/basis fields without checking fetched source content, and the public review helper accepts a caller-chosen reviewer label and approval. F-01 reproduces that path. F-02 reproduces a separate malformed numeric value entering the same trusted view.

The SQL view does independently filter for approved decision, valid quality, eligible unit, point price type, and a verified identity state/key. The implemented checks protect against invalid enum values and unsupported currency, but they do not make the supporting claims authentic: the repository can accept those fields from a forged model, and the `reviewer` value is not authenticated in this code. Treat the view as policy filtering, not as proof that the candidate and reviewer claims came from trusted sources.

## Review D — Schema validator

I changed actual DDL/object definitions on copies of a valid isolated Migration 003 database. The validator rejected changed string literals, collations, defaults, foreign-key actions, partial-index predicates, expression-index expressions, trigger bodies, and view definitions. Existing tests also rebuild Foundation constraints and confirm that missing UNIQUE, CHECK, and foreign-key constraints are rejected. Harmless whitespace/comments normalized. The exact Worker 027 CHECK literal mutation and unexpected blocking trigger also failed before registry or backup creation.

The demonstrated limitation is false rejection of equivalent identifier casing/quoting. No tested meaningful drift was falsely accepted. Because this implementation is a tokenizer plus PRAGMA comparison rather than SQLite’s parsed semantic representation, passing fixtures should not be treated as proof of complete schema equivalence.

## Review E — Backup and restore integrity

The exported backup helper rejects a wrong target directly, before creating files. Focused tests reproduced environment override and alias protections. Backup manifests contain a backup ID, filename/hash, resolved intended target path identity, purpose, schema and migration registry state, timestamp, and HMAC. Verification rejected unsigned manifest tampering, backup-content tampering, a target mismatch, a signed but false schema-state claim, and a healthy unmanifested source. The restore target remained unchanged in the rejection tests.

Manifest verification checks the signed target path identity and compares the backup’s actual schema/migration snapshot to the signed snapshot. It binds to the canonical path, not a persistent filesystem file ID; the registry snapshot includes migration checksums and applied/baselined states. This is adequate for the tested path-based workflow when the local HMAC key remains trustworthy. The code records `migration_version_purpose`, but does not independently validate that field against the schema snapshot.

The restore helper requires `writers_stopped=True`, but that is an operator acknowledgment and not a live-writer check. README guidance explicitly says to stop writers/connections and checkpoint/close WAL databases. Isolated WAL/SHM sidecars were preserved beside the prior database in the focused tests. F-04 records the staged-verification cleanup gap; the target itself remained byte-for-byte preserved.

## Review F — Tests and production read-only audit

Test setup/teardown and the full-suite benchmark path were inspected before execution. No tests were skipped or failed. The `-B` flag prevented Python bytecode writes during these commands.

| Command | Exit | Tests | Duration |
|---|---:|---:|---:|
| `\.venv\Scripts\python.exe -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q` | 0 | 85 | 30.028 s |
| `\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` | 0 | 211 | 97.239 s |

Production read-only audit before and after all tests:

| Check | Before | After | Result |
|---|---|---|---|
| SHA-256 | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | same | matches required baseline |
| `PRAGMA integrity_check` | `ok` | `ok` | pass |
| `PRAGMA foreign_key_check` | 0 violations | 0 violations | pass |
| Migration 002 / 003 | ABSENT / ABSENT | ABSENT / ABSENT | preserved |
| `schema_migrations` / `user_version` | absent / 0 | absent / 0 | preserved |
| WAL / SHM sidecars | absent / absent | absent / absent | preserved |

All table counts before and after matched Worker 028’s supplied baseline: sellers 3; marketplace_listings 17; marketplace_listing_observations 51; facebook_ads 162; facebook_ad_observations 217; products 0; product_aliases 0; product_mappings 0; legacy_products 18; legacy_sellers 3; legacy_marketplace_listings 17; legacy_price_history 0; legacy_advertising_campaigns 69.

No production backup, migration, capture, recovery, rollback, restore, or write-capable database connection was used. Production Migration 002 and 003 were not applied. The normalization manifest and Worker 027 review artifacts retained their Worker 028-reported hashes. No production-path backup was created.

## Final verdict

**NO-GO**

The specific unsupported `XYZ` regression is fixed, and the Migration 003 lifecycle is transactionally sound in the tested existing-registry scenario. However, caller-forged supported currency/basis and malformed nonnumeric prices can still reach the trusted-price view. These defects cross the trust boundary Migration 003 is intended to establish. Do not proceed to separate production-migration approval until the issues above are remediated and independently reproduced as closed.
