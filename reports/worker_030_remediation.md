# Worker 030 — Remediation Report

**Date:** 2026-10-14 (Asia/Riyadh)  
**Reviewed input:** Worker 029 NO-GO review and machine-readable results  
**Production migration authorized or performed:** No  
**Verdict:** **READY WITH CONDITIONS**

This remediation addresses F-01 through F-05 on isolated databases. It does not
approve a production migration or declare the system production-ready. The
condition is that an authorized review service provisions and protects the
review-signing key out of band before any approval can be recorded. Without that
key, approved reviews fail closed.

## Findings

### F-01 — Currency and review provenance: FIXED

`append_landing_page_fetch()` now binds the fetch to the destination recorded
on the exact Facebook observation and stores the submitted source bytes with
their SHA-256 hash. `append_facebook_price_evidence()` verifies the stored
content hash and re-runs the fixed JSON-LD extractor. Caller-supplied candidate
currency, basis, identity labels, evidence metadata, extraction method, and
version are ignored as provenance. A missing body, unreadable body, hash
mismatch, unsupported currency, or contradictory currency leaves structured
currency unknown and the evidence untrusted.

Approvals are separate from extraction. `APPROVED` reviews require an
HMAC-SHA256 authorization over the evidence ID, reviewer label, decision,
review fields, and complete evidence object. Migration 003's insert trigger
checks the signature verifier. A currency override marked
`INDEPENDENTLY_VERIFIED` also requires a distinct source URL, source content,
matching content hash, and explicit supported-currency marker. The review
signing key is supplied by a trusted service and is never auto-created.

Reproduction and evidence:

1. `tests.test_price_evidence.OuedknissPriceEvidenceTests.test_worker_029_supported_currency_claims_need_source_or_signed_independent_evidence`
   reproduces both Worker 029 claims: DZD/INDEPENDENTLY_VERIFIED against a page
   with no currency, and DZD/SOURCE_CONFIRMED against page content declaring
   XYZ. Both remain out of `trusted_price_evidence`. A source-confirmed review
   cannot promote the contradictory row. A signed, evidence-backed independent
   review for DZD can promote either row.
2. `tests.test_price_evidence.OuedknissPriceEvidenceTests.test_worker_027_forged_facebook_currency_cannot_become_trusted`
   confirms a caller-created model does not supply stored currency provenance.
3. `tests.test_price_evidence.OuedknissPriceEvidenceTests.test_facebook_fetch_cannot_rebind_content_to_a_different_destination`
   confirms a caller cannot attach content to a different URL than the exact ad
   observation records.
4. The focused suite also preserves supported DZD, EUR, and USD source paths in
   `test_currency_write_boundary_accepts_only_canonical_supported_codes`.

### F-02 — Malformed amounts: FIXED

The repository validates runtime amount types before persistence. It rejects
strings, booleans, NaN, positive or negative infinity, zero, negative values,
and amounts above the documented `1e12` ceiling. Parsed source amounts outside
policy retain their raw source content while their structured amount is nulled
and marked as an extraction error.

Migration 003 adds an insert trigger requiring numeric SQLite storage classes,
finite positive values within `1e12`, and a valid range maximum. The trusted
view independently requires `typeof(candidate_amount)` to be `integer` or
`real` and enforces the same positive range. Thus a text value cannot become a
trusted price even if inserted below the normal trigger boundary.

`tests.test_price_evidence.OuedknissPriceEvidenceTests.test_malformed_candidate_amount_is_rejected_at_api_and_database_boundaries`
reproduces `candidate_amount='not-a-number'`, exercises the invalid runtime
types and limits, verifies the insert trigger rejects text, then bypasses that
trigger in an isolated fixture and confirms the trusted view still excludes the
text row.

### F-03 — Identifier equivalence: FIXED WITH A DOCUMENTED DIALECT

Schema validation now normalizes SQLite ASCII identifier casing across object
names, columns, foreign keys, indexes, and DDL tokens. Ordinary non-keyword
identifiers compare equivalently when unquoted or quoted with double quotes,
backticks, or brackets. String literals and quoted keywords retain their
meaning. The validator documents its tokenizer limits and fails before
baseline/migration changes when a definition falls outside the accepted
dialect.

`tests.test_foundation_v2.FoundationSchemaSafetyTests.test_ascii_identifier_case_and_quoting_equivalence_is_accepted`
accepts a `PRODUCTS`/`"CANONICAL_NAME"` spelling. The companion
`test_meaningful_identifier_drift_is_still_rejected` and existing literal-drift
test reject meaningful changes before baseline. Unicode case-folding,
quoted-keyword role inference, SQLite extensions, and arbitrary parser rewrite
equivalence remain outside the documented dialect.

### F-04 — Restore staging cleanup: FIXED

Restore now handles copy, staged verification, replacement, and final
verification within one exception-safe recovery path. It restores the original
database and sidecars after replacement-stage failures, removes the staged file
when safe, and reports the exact remaining stage path and OS error if cleanup
fails.

`tests.test_migration_lifecycle.MigrationLifecycleTests.test_restore_failure_at_copy_verification_and_replacement_preserves_target_and_sidecars`
injects copy, staged verification, target replacement, and post-install
verification failures. Each case confirms the target and `-wal`/`-shm` bytes
remain unchanged and removes the stage. The adjacent
`test_restore_reports_exact_stage_path_when_cleanup_fails` confirms the
remaining path and cleanup reason are included in the diagnostic.

### F-05 — Missing manifest key: FIXED

Manifest verification now reads but never creates or replaces the signing key.
Missing and invalid material produce explicit diagnostics. New key creation is
a separate, intentional `initialize_backup_manifest_signing_key()` operation;
backup creation checks for an existing valid key before creating backup files.

`tests.test_migration_lifecycle.MigrationLifecycleTests.test_missing_manifest_key_verification_fails_without_creating_replacement`
deletes the isolated key, verifies that backup checking reports the missing
key without recreating it, confirms invalid existing bytes remain unchanged,
and confirms new backup creation fails without producing a backup when the key
is absent.

## Migration and compatibility validation

- Migration 001 and Migration 002 SQL files were not edited. Their current
  SHA-256 values match the recorded values.
- Migration 003 remains forward-only and is applied transactionally. Failure
  injection after each of its seven SQL statements rolled back schema and
  registry changes; retry succeeded and source-observation fingerprints stayed
  unchanged.
- Migration lifecycle retry, checksum-drift, incomplete-registry, incomplete
  schema, and restore tests passed on temporary isolated databases.
- Append-only evidence and observation history remain covered by the focused
  suites. No production catalog or trusted-price fixtures were added.
- Existing Worker 027 review/results and normalization manifest hashes match
  the hashes recorded in Worker 029. Worker 029 review/results were not edited.

| Migration | Current checksum | Recorded checksum | Result |
|---|---|---|---|
| 001 `001_foundation_v2.sql` | `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` | Same | Match |
| 002 `002_price_evidence.sql` | `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844` | Same | Match |
| 003 `003_price_evidence_trust_policy.sql` | `f91fb42af6d2f9556fafe6252b7395c85bb3e1778abc11195f5f1fa5a51245b6` | Not recorded in production | Remediation version |

## Tests

| Run | Result | Tests | Duration |
|---|---|---:|---:|
| Focused suites: foundation, migration lifecycle, migration CLI safety, price evidence, Ouedkniss sensor | Passed | 93 | 40.062 s |
| Full suite: `python -B -m unittest discover -s tests -q` | Passed | 219 | 99.816 s |

The full-suite normalization benchmark opens the production database read-only
and writes only to a temporary benchmark copy. The benchmark temporary
directory was cleaned up. Migration and restore mutations used temporary
isolated databases.

## Production read-only audit

The before and after audits used `mode=ro` with `PRAGMA query_only=ON`. No
write-capable connection was opened against production.

| Check | Before | After | Result |
|---|---|---|---|
| Production SHA-256 | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | Same | Unchanged |
| `PRAGMA integrity_check` | `ok` | `ok` | Pass |
| `PRAGMA foreign_key_check` | 0 violations | 0 violations | Pass |
| Foundation 001 schema | Applied, registry absent | Same | Unchanged |
| Migration 002 | Absent | Absent | Unchanged |
| Migration 003 | Absent | Absent | Unchanged |
| `schema_migrations` | Absent | Absent | Unchanged |
| `PRAGMA user_version` | 0 | 0 | Unchanged |
| `-wal` / `-shm` sidecars | Absent / absent | Absent / absent | Unchanged |

| Table | Before rows | After rows |
|---|---:|---:|
| `sellers` | 3 | 3 |
| `marketplace_listings` | 17 | 17 |
| `marketplace_listing_observations` | 51 | 51 |
| `facebook_ads` | 162 | 162 |
| `facebook_ad_observations` | 217 | 217 |
| `products` | 0 | 0 |
| `product_aliases` | 0 | 0 |
| `product_mappings` | 0 | 0 |
| `legacy_products` | 18 | 18 |
| `legacy_sellers` | 3 | 3 |
| `legacy_marketplace_listings` | 17 | 17 |
| `legacy_price_history` | 0 | 0 |
| `legacy_advertising_campaigns` | 69 | 69 |

The original protected database and its embedded legacy tables remain
byte-for-byte unchanged. No new production backup was created. No production
migration, capture, recovery, restore, rollback, or other write operation was
performed. Existing data-directory backups predate this task and were left
untouched.

## Conditions and verdict

Configure the HMAC review key only in the trusted review service, provision it
out of band, and keep it unavailable to ordinary evidence callers. Without a
valid signature, the repository and Migration 003 approval trigger reject
approvals. The database audit did not create or install a production review
key.

**READY WITH CONDITIONS** — ready for a fresh independent review of the
remediation. This verdict is not production migration approval.
