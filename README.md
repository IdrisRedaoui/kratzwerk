# Kratzwerk Data Foundation V2

## Schema assessment

The audited `Database.sqlite` had 18 `Products`, 17 `Marketplace_Listings`, 3
`Sellers`, 69 `Advertising_Campaigns`, and no `Price_History` records. SQLite's
integrity check passed, but foreign key enforcement was off on the audit
connection and the database declared no observation uniqueness. Marketplace
listings required a product and cascade-deleted with it; ads also required a
product. Consequently raw source records could not exist independently.
`current_price` was stored on the listing row, not as a time series. A source
listing row with a price of zero is not reliable evidence of a free item.
Advertisements were named campaigns and linked to products even though the
input only established individual ad URLs.

The supplied database was inspected read-only. Its complete table and index
definitions remain in `legacy_*` tables after migration.

## V2 architecture

- `marketplace_listings` and `facebook_ads` store independent source entities;
  both allow a null `product_id`.
- `marketplace_listing_observations` and `facebook_ad_observations` append
  timestamped snapshots. `(entity_id, observed_at)` prevents duplicate snapshots
  for the same exact instant while allowing later observations. SQLite triggers
  reject updates and deletes on observations.
- `products` contains only canonical products. Migration does not promote
  legacy product rows or attach listings/ads to them.
- `product_aliases` and `product_mappings` provide a future mapping foundation,
  with confidence, provenance, and optional alias references. No normalization
  logic is implemented.
- `sellers` records platform identity separately from display name. A stable
  platform seller ID remains nullable until the source provides one.
- `legacy_*` preserves every old row and original table definition for audit
  and rollback. Legacy IDs are reused in the new source rows where practical.

Migrated listings receive one baseline observation at their stored `last_seen`;
their `first_seen_at` remains null because the old schema did not record it.
Positive legacy amounts are retained; a legacy zero is stored as null and
flagged as unknown in source payload metadata. The old schema had no currency
field, so Migration 003 exposes each Migration 001 amount through
`legacy_currency_unconfirmed_observations` with currency basis `UNKNOWN`. It
does not rewrite the append-only Migration 001 observations. A migrated amount
cannot become trusted price evidence without an approved independent currency
review. All 69 legacy ad URLs are
represented as ads (not campaigns), left unmapped, and receive an import-time
observation. The legacy start date is retained as `start_date`; it is not
misrepresented as first-seen time. Import time is used for `first_seen_at` and
`last_seen_at` because the old schema had no observation timestamps.

## Central database path

All Python consumers should import `connect` and `DATABASE_PATH` from
`config.py`. Set `KRATZWERK_DB_PATH` in the environment to the same absolute
SQLite path used by DataGrip. The fallback is `./data/Database.sqlite` relative
to this project. The development database path is intentionally not embedded
in the module or sensors. Connections enable SQLite foreign key enforcement.

Example:

```python
from config import connect

with connect() as db:
    rows = db.execute("SELECT * FROM marketplace_listings").fetchall()
```

## Versioned migration and restore

`migrations/001_foundation_v2.sql` renames old tables to `legacy_*`, creates
the new schema, and copies source records without inventing product matches.
`migrate.py` tracks Migrations 001, 002, and 003 in `schema_migrations`.
Each migration gets its own verified SQLite online backup and transaction.
The runner checks migration checksums, schema objects, integrity, foreign keys,
and unchanged source-row fingerprints. An untracked existing schema is recorded
as a verified baseline with an unknown original application time. Migration 003
adds currency allowlist guards, positive finite amount enforcement, an approval
authorization check, the trusted-view currency allowlist, and the legacy
currency-unknown assessment view. Migration 002 remains unchanged so an
installation with its checksum recorded can apply Migration 003.

The schema validator accepts SQLite ASCII identifier case equivalence and
equivalent quoting of ordinary non-keyword identifiers using double quotes,
backticks, or brackets. It preserves string literal meaning and does not infer
roles for quoted keywords or Unicode case-folding. Definitions outside this
dialect are reported as schema drift before a baseline or migration is changed.

Facebook price evidence stores submitted fetch bytes beside their SHA-256 hash.
The repository verifies that hash and re-runs the documented JSON-LD extractor
from that content; caller-created candidates, currency labels, and extractor
metadata do not establish provenance. A structured candidate amount must be a
finite numeric value greater than zero and no greater than `1e12`. Raw fetch
content and unparseable values remain available for audit, while the trusted
view excludes malformed SQLite storage classes.

An `APPROVED` review requires an HMAC-SHA256 signature from an explicitly
provisioned review authority. Set `KRATZWERK_PRICE_REVIEW_HMAC_KEY` to the
out-of-band key (at least 32 UTF-8 bytes) in the trusted review service. The
signature covers the evidence ID, reviewer label, decision, all structured
review fields, and the complete evidence object. The repository exposes
`price_evidence_review_authorization_payload()` so the authorized signer can
form the canonical payload; ordinary review calls without a valid signature
cannot create an approval. A currency override marked
`INDEPENDENTLY_VERIFIED` must include a distinct source URL, its exact content,
the content SHA-256, and the same supported currency code in the signed
`currency_evidence` array. Missing or contradictory evidence remains currency
unknown unless that signed review supplies separate evidence.

```powershell
python migrate.py status --db 'C:\path\to\isolated-copy.sqlite' --isolated-db
python migrate.py preflight --db 'C:\path\to\isolated-copy.sqlite' --target 3 --isolated-db
python migrate.py apply --db 'C:\path\to\isolated-copy.sqlite' --target 3 --isolated-db
python migrate.py restore --db 'C:\path\to\isolated-copy.sqlite' --backup 'C:\path\to\verified-pre-migration.bak' --isolated-db --writers-stopped
```

Migration commands default to production mode and bind `--db` to the protected
project identity `data/Database.sqlite`. The protected identity is derived from
the project location and is independent of `KRATZWERK_DB_PATH`. Relative paths,
case variants, symlinks, junctions, and file aliases that resolve to that file
are treated as the same target. A different target is rejected before preflight,
backup creation, registry writes, or schema changes, with:

> Migration command refused: --db must resolve to the protected production database 'C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\Database.sqlite'; pass --isolated-db only for a non-production database.

For a disposable copy, pass `--isolated-db`. The exported `backup_database()`
helper enforces the same target policy directly; callers must pass
`isolated=True` for disposable copies. This opt-in is a command-line or
explicit function argument, cannot come from an environment variable, and
still rejects the protected production file and its aliases with:

> Migration command refused: --isolated-db cannot target the protected production database 'C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\Database.sqlite'.

Each backup has a `.manifest.json` sidecar binding its content hash, intended
database path identity, migration purpose, and verified schema/registry state.
Restore rejects missing, altered, mismatched, or incompatible manifests before
staging or replacing the target. A restore command also requires
`--writers-stopped`. This is an operator acknowledgment, not proof that no
writer is connected. The manifest HMAC key is kept in the local user's
application-data directory, outside the backup folder. Preserve that key with
the same care as the backup. Create it intentionally, before the first signed
backup, with `migration_lifecycle.initialize_backup_manifest_signing_key()`.
Backup verification never creates or replaces key material; if the expected
key is missing or invalid, verification reports that condition and stops. If
it is unavailable on the restore host, automated restore fails closed and the documented manual recovery procedure applies. If a
backup has no verifiable provenance manifest, do not
use the automated restore helper. The supported recovery alternative is an
offline, separately reviewed manual recovery: compare the candidate against
the incident's intended target and known table/source fingerprints, preserve
the current database and sidecars, and have the database operator restore only
after independently establishing which backup belongs to that target. SQLite
integrity alone is not evidence of backup provenance.

Migration 002/003 are restored from their verified pre-migration backups. Foundation V2
rollback is disabled until an independently verified destructive procedure is
available. Stop all writers and connections before an on-disk restore. In WAL
mode, checkpoint and close the database first; the restore keeps the previous
database and any `-wal`/`-shm` sidecars beside the restored file for recovery
review.

The rollback refusal is reported as:

> Foundation V2 rollback is disabled: no independently verified destructive rollback procedure is available. Stop writers and restore a verified pre-migration backup instead.

The Ouedkniss capture command checks the complete Migration 002 schema and
Migration 003 trust policy against the configured `KRATZWERK_DB_PATH` before
network collection and before writes. It fails closed when either schema is
missing or incomplete and never runs a migration.
Historical recovery is separate and must be run against an isolated database
copy after migration:

```powershell
python recover_ouedkniss_price_evidence.py --db 'C:\path\to\isolated-copy.sqlite'
```

Historical recovery always refuses the protected production database, even if
`KRATZWERK_DB_PATH` is overridden, and also refuses the currently configured
database. Production-target refusal is reported as:

> Recovery refused: the protected production database cannot be used, regardless of KRATZWERK_DB_PATH; use a separate isolated copy.

An attempt to recover into the currently configured database reports:

> Recovery refused: use a separate isolated copy of the configured database.

Price-evidence extractors and database write guards currently recognize
`DZD`, `EUR`, and `USD`. Other values, including syntactically valid but
unrecognized three-letter codes, are retained as raw evidence with a null
structured code and unknown basis. The trusted-price view independently
requires a supported code, an eligible basis, an acceptable unit and price
type, reliable product identity, and an approved review.

## Validation

Run the included isolated migration tests with:

```powershell
python -m unittest discover -s tests -v
```

The tests use a temporary SQLite database and cover legacy data preservation,
unmapped source entities, zero-price handling, observation history and
deduplication, foreign keys, cross-source product linkage, and rollback.

See [SENSOR_V2_INTEGRATION.md](SENSOR_V2_INTEGRATION.md) for a field-by-field
assessment of the existing sensors and their V2 integration requirements.
