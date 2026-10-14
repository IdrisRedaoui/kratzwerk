# Worker 031 — Fresh Independent Review of Worker 030 Remediation

**Review date:** 2026-10-14 (Asia/Riyadh)
**Reviewed input:** Worker 029 independent review and Worker 030 remediation
**Review basis:** Current repository implementation, Migration 003, focused/full suites, isolated reproductions, and a read-only production audit
**Verdict:** **NO-GO**

This review does not authorize a production migration. Worker 030 closed the original nonnumeric-text amount defect and several migration/restore issues, but F-01 remains open: submitted fetch bytes and metadata are not authenticated, and Migration 003's approval trigger can be satisfied by a caller-registered SQLite function that always returns true. F-02 is partially closed; SQLite affinity also turns Python `True` into a valid-looking numeric `1.0` on a direct SQL path.

## Scope and safety

I inspected the actual implementation and did not treat Worker 029/030 reports as proof. All mutation tests used temporary databases and disposable test material. The only production database access was through a `mode=ro` SQLite URI with `PRAGMA query_only=ON`, plus file hashing and sidecar existence checks. The original legacy database was not opened or modified. I did not apply Migration 002 or 003 to production, create or provision a production review-signing key, record production approvals, create production backups, or run production capture, recovery, restore, rollback, or other write-capable operations. No code or migration was changed.

## Findings

### F-01 — Currency and approval provenance: FAIL

**Worker 030 claim:** Caller extraction fields are ignored; exact fetched content is hash-checked and re-extracted; signed review approvals are independently enforced.

**Confirmed behavior:** `append_facebook_price_evidence()` does ignore caller-supplied candidate currency, basis, identity, extraction labels, and metadata, and derives those values again from the fetch record's stored bytes. The content hash check detects later content/hash mismatch. However, `append_landing_page_fetch()` explicitly performs no network request and accepts `content`, `resolved_url`, `fetcher_name`, `fetcher_version`, and fetcher `evidence` from its caller. It computes a hash over those submitted bytes itself. The hash proves the stored bytes match the caller's submission; it does not authenticate the origin or that the bytes came from the destination URL.

**Isolated reproduction:** I seeded an ad observation with destination `https://shop.example/observed-item`, then called the public fetch API with fixture HTML, `resolved_url='https://attacker.example/fabricated.html'`, `fetcher_name='trusted_network_fetcher'`, and caller-supplied metadata claiming `network_io` and `tls_verified`. The API stored those claims and locally computed a matching hash. A caller-created extraction claiming EUR 1.00, a forged product, and extraction version `999` was ignored; the persisted evidence was instead 1299.9 DZD from the caller-submitted HTML, marked `SOURCE_CONFIRMED`, with the forged resolved URL and fetch metadata retained as claims. This confirms candidate re-extraction works while source-origin provenance does not.

**Approval trigger bypass:** On that temporary Migration 002/003 database, an unsigned direct `INSERT` of an `APPROVED` review failed on an ordinary connection with `no such function: kratzwerk_price_review_authorized`. I then registered the connection-local function under that expected name with `lambda *args: 1` and repeated the direct insert with reviewer `untrusted-caller`, no signature, and empty supporting evidence. The insert succeeded and `trusted_price_evidence` returned the price. Migration 003's trigger therefore checks only whether a connection provides a function with a chosen name and a truthy result; it does not itself verify the HMAC. No signing key was needed for this reproduction.

**Signing design inspection:**

- The review key is read from process-wide `KRATZWERK_PRICE_REVIEW_HMAC_KEY`; the repository contains no separate signer service or identity provider. `append_price_evidence_review()` requires a caller-supplied signature, and there is no public signing endpoint, but any code running in the same Python process can read its environment and Python's underscore-prefixed helpers are importable. The reviewer field is only a string; authentication of that identity must happen outside this repository.
- The HMAC payload includes the exact evidence ID and review decision, reviewer label, review fields, reasons, and review evidence. It does not serialize the underlying `price_evidence` row into the payload; the evidence ID is the pointer to that append-only row. The current append-only triggers preserve the pointer under ordinary repository use.
- Serialization uses sorted keys and compact JSON, which is deterministic for ordinary JSON values. It is not a strict canonical JSON format (`json.dumps` keeps its default `allow_nan=True`), and the UDF parses stored JSON before reserializing it.
- A valid signature is bound to the evidence ID, decision, reviewer, and changed currency/review fields, so it does not replay across those changes through the repository verifier. The key ID is only the first 16 hex characters of SHA-256(key). There is no configured key ring, rotation procedure, or revocation behavior: changing the environment key rejects new signatures made with the old key, while previously inserted approved rows remain eligible in the view.
- The trigger's connection-local UDF does not protect direct lower-level writers that can register their own function. The reproduction above disproves the report's assertion that the trigger independently enforces authorization.

**Disposition:** F-01 is not resolved. Re-extraction protects against forged candidate objects but the public fetch writer authenticates neither origin nor the reviewer, and the SQL trigger has a reproducible unsigned approval bypass. Migration 003 is not safe to submit for a separate approval decision on this evidence.

### F-02 — Numeric price enforcement: PARTIAL

The public repository validation rejects strings, booleans, NaN, infinities, zero, negative values, and values above 1e12. Migration 003 rejects nonnumeric stored classes, nonpositive values, infinities/out-of-policy amounts, and invalid range maxima at insert. The trusted view also requires `typeof(candidate_amount)` to be `integer` or `real`, a positive amount no greater than 1e12, and no range maximum.

**Isolated direct-SQL probes:**

| Python value bound to SQLite | Result |
|---|---|
| `'not-a-number'`, `b'100'` | Rejected by `trg_price_evidence_amount_policy` |
| `True` | Accepted and stored as REAL `1.0` due to SQLite affinity/conversion |
| `float('nan')` | Accepted as NULL; the trusted view excludes NULL amounts |
| positive/negative infinity, `0`, `-1`, `1e12+1` | Rejected |
| numeric string `'100'` | Coerced to REAL `100.0` and accepted as a valid numeric amount |
| `1e12` | Accepted at the inclusive ceiling |

The Worker 030 regression test also drops the amount trigger in an isolated fixture, inserts nonnumeric text, and confirms the trusted view excludes it. Thus the original F-02 text-storage exploit is closed. However, the Python public API rejects `True` while a direct SQLite binding silently turns it into numeric `1.0`, which passes the database trigger and view's numeric type/range predicates. SQLite cannot distinguish that bound boolean from integer `1` after affinity. The strict requirement to reject or exclude booleans on every lower-level storage path is therefore not fully met. Numeric-looking strings likewise become numeric storage rather than remaining text.

### F-03 — Schema equivalence and drift: PASS (within documented dialect)

The focused foundation suite passed the identifier-case/quoting equivalence test: an isolated `PRODUCTS` table and quoted `"CANONICAL_NAME"` spelling were accepted. The companion meaningful-column rename and CHECK-literal case mutation tests were rejected before baseline/backup creation. The suite also checks required constraints, foreign keys, indexes, and triggers. Worker 030 documents a deliberately limited dialect: SQLite ASCII identifier case equivalence and ordinary non-keyword identifiers quoted with double quotes, backticks, or brackets; it does not claim arbitrary parser-rewrite equivalence.

No tested meaningful schema drift was accepted. A tokenizer is not a complete SQLite semantic parser, so this pass applies only to the documented dialect and tested drift cases.

### F-04 — Restore failure safety: PASS

The focused migration-lifecycle suite passed injections at stage copy, staged verification, target replacement, and post-install verification, including target and `-wal`/`-shm` preservation and stage cleanup checks. I additionally injected an exception after writing partial bytes to the restore stage. On a temporary fixture, target bytes and both sidecars remained byte-for-byte unchanged and no `.restore-stage-*.sqlite` file remained. No production restore was attempted.

### F-05 — Manifest signing key lifecycle: PASS

The focused suite passed checks that a missing key remains missing during verification, invalid bytes remain unchanged, and backup creation with a missing key creates no backup artifact. Additional isolated verification covered a changed key and a wrong valid-length key: both failed with the specific `Backup provenance manifest signature is invalid` diagnostic; key bytes and the set of temporary files remained unchanged during verification. All manifest and database files were in a disposable temporary directory. No production key or backup was created.

## Migration lifecycle and compatibility

The isolated lifecycle tests passed `001 → 002 → 003`, failure injection after each of Migration 003's seven SQL statements, transactional rollback to versions 1/2, and successful retry. They also passed checksum drift, incomplete schema/registry rejection, and forward-only rollback refusal. These results do not imply production migration approval.

I independently recomputed the migration-file hashes with `Get-FileHash -Algorithm SHA256`:

| Migration | Current SHA-256 | Comparison |
|---|---|---|
| `001_foundation_v2.sql` | `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` | Unchanged; matches recorded value |
| `002_price_evidence.sql` | `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844` | Unchanged; matches recorded value |
| `003_price_evidence_trust_policy.sql` | `f91fb42af6d2f9556fafe6252b7395c85bb3e1778abc11195f5f1fa5a51245b6` | Current checksum independently recorded; not present in production registry |

## Tests and reproductions

| Command | Exit | Result | Duration |
|---|---:|---:|---:|
| `\.venv\Scripts\python.exe -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q` | 0 | 93 tests passed | 44.242 s |
| `\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` | 0 | 219 tests passed | 107.643 s |

The suites include Worker 029's supported-currency claim reproductions, Worker 030's amount/schema/restore/key regressions, Migration 003 rollback/retry and drift tests, and the migration compatibility tests. The new direct approval-trigger, caller-supplied fetch provenance, SQLite amount-affinity, changed/wrong manifest-key, and partial-stage-copy probes used inline Python against disposable fixtures. No dependency was installed; `-B` suppressed bytecode writes. No requested test suite was skipped.

## Production read-only audit

Both audits used a `file:` URI with `mode=ro` and `PRAGMA query_only=ON`; no write-capable production connection was opened.

| Check | Before | After | Result |
|---|---|---|---|
| SHA-256 | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | same | Matches supplied baseline; unchanged |
| `PRAGMA integrity_check` | `ok` | `ok` | Pass |
| `PRAGMA foreign_key_check` | 0 violations | 0 violations | Pass |
| Migration 002 objects | absent | absent | Unchanged |
| Migration 003 objects | absent | absent | Unchanged |
| `schema_migrations` | absent | absent | Unchanged |
| `PRAGMA user_version` | 0 | 0 | Unchanged |
| WAL / SHM | absent / absent | absent / absent | Unchanged |

Table counts matched before and after: `sellers` 3; `marketplace_listings` 17; `marketplace_listing_observations` 51; `facebook_ads` 162; `facebook_ad_observations` 217; `products` 0; `product_aliases` 0; `product_mappings` 0; `legacy_products` 18; `legacy_sellers` 3; `legacy_marketplace_listings` 17; `legacy_price_history` 0; `legacy_advertising_campaigns` 69.

## Final verdict

NO-GO
