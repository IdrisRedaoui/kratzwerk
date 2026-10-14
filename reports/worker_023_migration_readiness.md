# Worker 023 — Price Evidence Migration Readiness & Capture Safety Audit

Audit date: 2026-10-11 (Asia/Riyadh)

## Recommendation: NO-GO

Migration 002 and both Ouedkniss capture branches passed functional checks on isolated databases. Do not deploy with the current migration lifecycle, however. The project has no numbered migration registry or backup-aware command for Migration 002; its helper can leave partial schema objects after an error, and a retry then fails. The existing migrate.py rollback path also fails on a Migration 002 database. Add an atomic, versioned Migration 002 runner and use a verified backup restore plan before production deployment.

No production migration or capture was run. No live Facebook page was fetched, no provider was called, and no dependency was installed.

## Part A — Actual implementation path

The GraphQL query in sensors/ouedkniss_sensor.py selects price, priceUnit, priceType, and status. It does not select currency. The default extract_price function validates the number, scales recognized units, and returns no currency. For price=1 and priceUnit=UNIT it returns numeric candidate 1 and currency=None.

_validated_price therefore sets currency=UNKNOWN and currency_basis=currency_unconfirmed. process_and_store_data writes the parsed number into marketplace_listing_observations.price only when the currency basis is source_response. With the current default extractor, that condition is false, so the legacy monetary column is NULL. The observation payload still contains the original announcement.price, announcement.price_unit, and announcement.price_type values.

After inserting observations, the sensor calls append_ouedkniss_evidence_for_capture inside the capture transaction. If the complete Migration 002 schema is absent, the helper returns zero without an error or warning and records no candidate. If installed, the helper reads the exact observation payload and appends an idempotent price_evidence row linked to that observation.

For a 1 UNIT source value, the new row preserves candidate_amount=1, raw value, and source unit UNIT; unit_status is UNRESOLVED, currency_code is NULL, and currency_basis is UNKNOWN. A single row receives UNIT_OR_CURRENCY_UNCONFIRMED quality. When at least three listings have the repeated 1 UNIT pattern at the same observation time, those rows receive SUSPICIOUS_PLACEHOLDER and REPEATED_ONE_UNIT_PATTERN_REVIEW. Neither case is a confirmed DZD price. The source FIXED label also adds PAYABLE_PRICE_NOT_ESTABLISHED_BY_SOURCE_TYPE.

Missing source prices remain NULL and produce extraction_status=MISSING and quality_status=MISSING. The schema migration itself creates no price evidence rows and does not backfill historical observations.

## Part B — Isolated Migration 002 checks

Created a fresh SQLite online-backup copy of the configured database at data/worker_023_migration_readiness.sqlite. Migration 002 applied successfully to that copy. Existing seller, listing, marketplace observation, Facebook ad, and Facebook observation rows were fingerprinted before and after; all row fingerprints, IDs, and counts matched. The new evidence and fetch tables remained empty, as expected for a schema-only migration.

| Table | Before | After |
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

Migration verification returned integrity_check=ok and zero foreign-key violations. All five Migration 002 indexes and all seven triggers were present. Representative invalid evidence rows were rejected for negative candidates, inverted ranges, malformed reason JSON, verified identity without a key, invalid extraction status, an observation from the wrong platform, and an invalid source-link shape. The append-only trigger rejected an evidence update.

Calling apply_price_evidence_migration a second time on the fully migrated copy was a no-op. An empty minimal Foundation V2-shaped database also accepted the migration. The trusted-price view excluded an unreviewed candidate; a fixture-only explicit approval with resolved currency, unit, and product identity made one test candidate eligible.

### Migration lifecycle incompatibilities

- migrations/002_price_evidence.sql contains no explicit transaction. apply_price_evidence_migration uses executescript, creates no backup, and checks foreign keys but not integrity.
- migrate.py only targets migrations/001_foundation_v2.sql. There is no shared migration version table or Migration 002 CLI flow.
- A complete helper rerun is safe because the helper recognizes all three new tables plus the view. A simulated late-object conflict showed a failed apply left earlier Migration 002 tables behind; retry then failed immediately with table facebook_landing_page_fetches already exists. Partial application is not atomic or retry-safe.
- On a separate migrated copy, migrate.py rollback failed with error in trigger trg_price_evidence_platform_guard: no such table: main.marketplace_listing_observations. Do not use that rollback command after Migration 002.
- The SQL comments require Foundation V2 first. The actual 002 objects also rely on marketplace_listings and its platform column, so the deployment preflight should check the full Foundation V2 schema, not only the subset checked by the helper.

## Part C — Ouedkniss capture safety

| Capture state | Verified behavior |
| --- | --- |
| Migration 002 absent | Synthetic capture succeeded with zero errors. The observation stored price=NULL and currency=UNKNOWN while preserving raw amount 1, unit UNIT, and type FIXED in its immutable payload. No price_evidence table or candidate existed. Repeating the same timestamp did not duplicate the observation. |
| Migration 002 applied | Three same-time 1 UNIT listings produced candidate amount 1 with NULL currency, UNKNOWN currency basis, UNRESOLVED unit, and suspicious-placeholder reasons. A missing-price listing remained NULL/MISSING. Repeating the capture added no observations or evidence. A later price=2 observation appended a new candidate and left earlier evidence and observations intact. No trusted price appeared without review. |

Operating this sensor before Migration 002 does not erase the raw amount or unit: both remain recoverable from the append-only source observation payload. It does silently omit the structured price candidate, normalized unit interpretation, extraction quality, and reason codes because append_ouedkniss_evidence_for_capture returns zero when the schema is absent. Migration 002 alone does not backfill those captures. They can be reconstructed later from the payload, but need an explicit backfill pass.

Minimal remediation: deploy Migration 002 before enabling this sensor version, and add a schema-readiness gate that prevents capture from silently omitting structured evidence. If any capture runs before migration, backfill its exact immutable observations after the migration.

## Part D — Safe deployment and restore sequence

1. Pause capture writers and verify the exact configured database path.
2. Create a SQLite online backup in separate storage. Verify the backup SHA-256, integrity_check=ok, foreign_key_check=0, baseline table counts, and source row/ID fingerprints.
3. Preflight the full Foundation V2 schema, Migration 002 name collisions or partial objects, JSON support, and available disk space. Stop if any subset of Migration 002 objects already exists.
4. Add and review a numbered Migration 002 runner that records the applied version, creates the pre-migration backup, enables foreign keys, executes inside BEGIN IMMEDIATE/COMMIT, and performs post-apply foreign-key and integrity checks. The current migrate.py command does not apply 002.
5. After apply, verify integrity, foreign keys, all new tables/indexes/triggers/view, and unchanged source counts and fingerprints. Expect zero historical price_evidence rows unless a separate reviewed backfill is intentionally run.
6. Run the price=1 UNIT, missing-price, duplicate-timestamp, and later-capture smoke cases against another isolated database copy.
7. If validation fails, stop writers and atomically restore the verified pre-migration backup. If captures happened after the backup, export and reconcile those writes first; restoring the backup would remove them. Do not use migrate.py --rollback after Migration 002.

## Production fingerprint and full suite

Production database SHA-256 before and after: 5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff. The read-only checks after the audit returned integrity_check=ok and zero foreign-key violations. All production table counts above were unchanged.

Full test suite: .\.venv\Scripts\python.exe -m unittest discover -s tests -q — 174 tests ran in 51.519 seconds; exit code 0.

## Deliverables

- reports/worker_023_results.json — machine-readable checks and fingerprints.
- data/worker_023_migration_readiness.sqlite — reproducible isolated copy with Migration 002 applied and no synthetic capture rows.
