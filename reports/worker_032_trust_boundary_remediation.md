# Worker 032 — Price Evidence Trust Boundary Remediation

**Date:** 2026-10-14 (Asia/Riyadh)
**Reviewed input:** Worker 031 independent review and results
**Production migration authorized or performed:** No
**Production review key provisioned:** No

## Disposition

Migration 004 closes the caller-forged fetch and SQLite UDF approval paths. The
repository has no real fetch service, reviewer identity provider, or isolated
approval signer. The implementation therefore keeps those trust states closed:
caller-submitted fetches are audit input, approval writes are refused, and the
trusted-price view returns no rows until controlled services are added and
independently reviewed.

| Finding | Status | Result |
|---|---|---|
| F-01A — Fetch origin and destination | **FIXED** | Caller-submitted content and metadata remain unverified and cannot establish `SOURCE_CONFIRMED` currency or trusted Facebook prices. No fetch service is fabricated. |
| F-01B — Review authorization | **PARTIAL** | The UDF bypass is removed and approvals are closed. A real authenticated reviewer and protected signing service, key lifecycle, and verified persistence path do not exist yet. |
| F-02 — Numeric integrity | **FIXED** | Public Python APIs reject booleans and malformed amounts. The documented SQLite affinity limitation is covered by regression tests; direct file writers are outside the supported trust boundary. |
| Migration lifecycle | **FIXED** | Migration 003 remains byte-for-byte unchanged. New Migration 004 is forward-only and transactional, with failure-injection, retry, incomplete-state, and checksum-drift coverage. |

**Threat model:** Ordinary evidence callers may invoke repository APIs and submit
arbitrary content and claims. They cannot establish source origin, set verified
fetch/review states through the supported API, approve prices, or obtain a
signing key. The trusted-price view filters for both a verified review state and
(for Facebook evidence) a verified fetch state. Anyone with unrestricted write
access to the SQLite file can modify schema and data, including dropping
triggers or changing rows. SQLite triggers do not authenticate such a writer.
Filesystem/process access controls and one controlled application write path
are required to protect the file boundary.

## F-01A — Fetch provenance

The repository contains no controlled network fetcher. `append_landing_page_fetch()`
does not make network requests. It now stores submitted bytes and caller claims
as audit input, writes `provenance_status='UNVERIFIED'`, and uses a fixed
`untrusted_caller_submission` fetcher label. Caller-supplied resolved URL, HTTP
status, content type, fetcher identity, and timestamp are not copied into
normalized trusted fields. They remain under `caller_claims` in the append-only
evidence record. The stored `fetched_at` is the repository receipt time; the
claimed fetch time remains audit metadata.

The content SHA-256 is checked against the submitted bytes for integrity only.
Evidence records explicitly state `source_origin_authenticated=false` and
`content_hash_proves_origin=false`. The extractor re-parses those bytes for
audit, but unverified Facebook results have null currency, `UNKNOWN` currency
basis and unit status, and `UNVERIFIED` product identity. Migration 004's view
requires `provenance_status='VERIFIED'`, successful HTTP status, HTML content
type, and a resolved URL. The current repository has no writer for that state.

The recorded destination on the exact Facebook observation must be an absolute
HTTP(S) URL without credentials, an unusual port, a local hostname, or a
non-public IP literal. A mismatched `original_url` is rejected. This check is
not DNS or redirect validation, and this API performs no request. The integration
contract for a future trusted fetch service requires it to bind to the exact ad
observation and its recorded destination; resolve and pin each address; recheck
every redirect; reject loopback, private, link-local, and other prohibited
targets; and attest the final URL, HTTP status, content type, and fetch time.
The service must authenticate its attestation. A local content hash or
caller-provided `tls_verified`/`network_io` value is never sufficient.

### Worker 031 fetch attack

Before the fix, an isolated fixture reproduced Worker 031's fabricated page:
caller HTML parsed to `1299.9 DZD SOURCE_CONFIRMED`, while the caller also
supplied an attacker-controlled resolved URL, trusted fetcher name/version,
`network_io=true`, and `tls_verified=true`. The locally computed hash matched
the caller's bytes.

After the fix, the same payload is retained with `provenance_status=UNVERIFIED`;
the normalized resolved URL/status/content type are null, and the fetcher name
is the fixed untrusted label. The body hash still matches the submitted bytes,
but is explicitly marked as integrity-only. The extracted amount `1299.9` is
retained for audit while currency is NULL/UNKNOWN and product identity is
UNVERIFIED. The price does not appear in `trusted_price_evidence`. Regression
tests also submit forged candidate amount/currency/identity fields and verify
that re-extraction ignores them.

## F-01B — Review authorization

Migration 004 replaces Migration 003's trigger that called
`kratzwerk_price_review_authorized`. No current trigger or trusted view invokes
that UDF. Registering a same-named SQLite function that returns 1 has no effect.
The new structural trigger rejects every `APPROVED` review and every claimed
`authorization_status='VERIFIED'`; ordinary repository calls can append only
unverified `REJECTED` or `NEEDS_MORE_EVIDENCE` proposals. The view requires
`authorization_status='VERIFIED'`. Existing Migration 003 review records
receive the new column's `UNVERIFIED` default, preserving their raw history
while removing their former trusted eligibility.

The public repository has no signing function and accepts no signature
parameter. An absent/empty review key does not enable an approval. Invalid,
modified-field, and cross-evidence replay signature attempts cannot enter
through the evidence API. Review JSON uses sorted keys and compact deterministic
serialization, rejects non-string object keys, and sets `allow_nan=false`, so
NaN and infinities cannot be serialized.

This finding remains **PARTIAL** because no real reviewer identity provider,
trusted approval service, or protected key exists to authorize valid approvals.
There is consequently no live key identity, rotation, revocation, or signature
verification policy to claim. Do not provision a key in the repository
process. The future integration must use an authenticated reviewer identity
and an isolated signer holding a private asymmetric key. Its signed statement
must cover the exact evidence ID and immutable evidence digest, reviewer,
decision, timestamp, all structured review fields, reason codes, and complete
review evidence. The controlled persistence path must validate strict
serialization and an explicit key ID against a versioned key registry, reject
revoked/unknown/invalid keys and missing configuration, and fail closed during
rotation. Only that process may persist a verified authorization state. Enabling
it requires a new reviewed forward migration; Migration 004 deliberately
cannot self-enable approval.

### Worker 031 spoofed UDF attack

Before the fix, an isolated fixture registered
`kratzwerk_price_review_authorized(...)=1`, inserted an unsigned approval for
`untrusted-caller`, and received a row from `trusted_price_evidence`.

After the fix, the same UDF is registered and the unsigned insert is rejected
with `trusted price review service is not configured`. The spoofed function is
not called, and the view contains no row. Tests also cover unsigned approvals,
fake/altered/replayed signatures at the repository boundary, missing key
configuration, and strict JSON rejection. These tests prove the current
fail-closed policy; they do not claim to validate cryptographic signing or key
lifecycle semantics that are not implemented.

## F-02 — Numeric integrity

The public Facebook evidence API validates each candidate amount before SQLite
binding and rejects both `True` and `False`, numeric-looking strings, BLOBs,
NaN/infinities, zero, negative values, and amounts over `1e12`. The inclusive
upper bound remains accepted. Extracted malformed values remain in raw
provenance when available, while invalid structured amounts are nulled or
rejected. Legitimate positive integers/floats through `1e12` remain representable.

The isolated SQLite binding regression observed:

| Bound Python value | SQLite result under REAL affinity |
|---|---|
| `True` | Accepted as `REAL 1.0`; SQLite cannot recover that it was a Python bool |
| `False` | Rejected by the positive amount constraint after coercion to zero |
| `100` / `123.5` | Accepted as numeric storage |
| `'100'` | Coerced to numeric `REAL 100.0` and accepted |
| `b'100'` | Rejected as BLOB storage |
| `NaN` | SQLite binding becomes NULL; accepted as missing, never as a trusted price |
| Positive/negative infinity | Rejected |
| `0` / `-1` | Rejected |
| `1e12` / `1e12 + 1` | Inclusive ceiling accepted / above-ceiling rejected |

The Migration 004 trusted view also filters to integer/real storage, finite
policy bounds, and a positive point price. These checks are defense in depth,
not proof of the original Python type. Direct SQL/file writers are outside the
supported trust boundary; a bound `True` and numeric `1` are indistinguishable
after SQLite affinity conversion. Protecting against arbitrary file writers
requires OS permissions, process isolation, and the controlled application
write path.

## Migration and compatibility

Migration 001, Migration 002, and Migration 003 were not edited. Their current
SHA-256 values match the Worker 031 recorded values. Migration 003's unsafe
assumption is corrected by adding Migration 004 rather than rewriting version
3; existing databases with a valid version-3 checksum can advance forward.
Migration 004 adds unverified state columns, blocks approval and verified-state
claims, replaces the view, and preserves existing evidence and review rows.
Its seven SQL statements execute in one migration transaction. Failure
injection after each statement rolled back to versions 1/2/3 with unchanged
source fingerprints; retry applied version 4. Tests reject incomplete
Migration 004 state and checksum drift. Signed pre-004 backup manifests remain
verifiable when they have no Migration 004 state key and the actual backup is
pre-004. Migration 001/002/003 remain byte-for-byte unchanged.

Migration 004 checksum: `f8677b4c76dd956f4316a12d4ec09d4b9161f54f06aa10201fa32a4e9eda03f3`.
Applying it to a nonproduction version-3 database changes the trusted view to
exclude all existing approvals until a future trusted review service is
introduced. No migration was applied to production, and no price approval or
production key was created.

| Migration | SHA-256 | Result |
|---|---|---|
| 001 `001_foundation_v2.sql` | `7444f12dc7941198a06a9e153bf7639f3a1b3c7311bfbd3726cad2dc077306b2` | Unchanged |
| 002 `002_price_evidence.sql` | `465a92382c634c45d8422b72ebd81d0c55a661b8d1e6ab86e32471736b201844` | Unchanged |
| 003 `003_price_evidence_trust_policy.sql` | `f91fb42af6d2f9556fafe6252b7395c85bb3e1778abc11195f5f1fa5a51245b6` | Unchanged; still rejected on its own for new trusted use |
| 004 `004_closed_price_trust_boundaries.sql` | `f8677b4c76dd956f4316a12d4ec09d4b9161f54f06aa10201fa32a4e9eda03f3` | New forward migration |

## Validation

The exact Worker 031 F-01 attacks were reproduced first on a disposable
Migration 002/003 fixture. All later mutations and security tests used temporary
databases. No dependencies were installed.

| Command | Result | Tests |
|---|---|---:|
| `python -B -m unittest tests.test_price_evidence -q` | Passed | 35 |
| `python -B -m unittest tests.test_migration_lifecycle -q` | Passed | 21 |
| `python -B -m unittest tests.test_foundation_v2 tests.test_migration_lifecycle tests.test_migration_cli_safety tests.test_price_evidence tests.test_ouedkniss_sensor_v2 -q` | Passed | 101 |
| `python -B -m unittest discover -s tests -q` | Passed | 227 |

The full suite's normalization benchmark was inspected: it reads the production
database with a read-only connection, runs mutations against a temporary copy,
and verifies the production hash is unchanged. `git diff --check` reported no
whitespace errors.

## Production and legacy-data audit

The before values below are the Worker 031 read-only baseline. The after audit
used the production SQLite file URI with `mode=ro` and `PRAGMA query_only=ON`;
the content hash was computed without opening a write-capable database
connection.

| Check | Before | After | Result |
|---|---|---|---|
| SHA-256 | `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` | same | Unchanged; matches required baseline |
| `PRAGMA integrity_check` | `ok` | `ok` | Pass |
| `PRAGMA foreign_key_check` | 0 | 0 | Pass |
| Migration 002 / 003 / 004 objects | absent / absent / absent | absent / absent / absent | Unchanged |
| `schema_migrations` / `user_version` | absent / `0` | absent / `0` | Unchanged |
| WAL / SHM sidecars | absent / absent | absent / absent | Unchanged |

| Table | Before | After |
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

All production checks matched. The original legacy records remain in the same
unchanged production file; no production write, backup, capture, recovery,
restore, rollback, migration, approval, or key provisioning occurred. All test
mutations used disposable databases.

## Conditions and verdict

Keep fetch and approval trust states closed. A fresh independent review should
verify Migration 004 and its tests. Implement and independently review the
fetch service, reviewer identity boundary, protected signer, key lifecycle,
controlled file access, and verified persistence path before enabling any
trusted prices or considering a production migration. This report does not
constitute production readiness or migration approval.

READY WITH CONDITIONS
