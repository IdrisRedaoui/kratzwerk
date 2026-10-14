# Worker 022 — Price Evidence Layer Implementation

Run date: 2026-10-11 (Asia/Riyadh)

## Scope and safety

This worker implements an additive price evidence model, conservative Ouedkniss
interpretation, and offline Facebook landing-page extraction from JSON-LD
fixtures. It does not fetch pages, run a capture, call a provider, install a
dependency, or change production data. Migration 002 was applied only to
isolated databases. The existing Worker 018 manifest and Worker 018/019/020/021
reports were left unchanged.

Configured production database: `data/Database.sqlite`.

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

Production SHA-256 before and after: `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF`.

## Existing schema and source findings

### Ouedkniss

- `sensors/ouedkniss_sensor.py` selects GraphQL fields `price`, `priceUnit`,
  `priceType`, and `status` from `FrontStoreQuery`. The query does not select a
  currency field.
- The source observation is `marketplace_listing_observations`. It stores
  `price`, a non-null `currency` defaulting to DZD, `observed_at`, and
  `source_payload_json`; `(listing_id, observed_at)` is unique and triggers
  prevent observation updates or deletes.
- The source payload preserves the raw numeric amount and source unit/type at
  `announcement.price`, `announcement.price_unit`, and
  `announcement.price_type`. Ouedkniss provides a numeric value here, not
  separate display text.
- Historical rows were written with `currency='DZD'` and
  `observation.currency_basis='assumed_from_Algerian_marketplace'`. The audited
  16 numeric rows are all `1 UNIT` across 8 listings and two capture times;
  these historical rows were not edited.
- The old sensor parser scaled recognized amount units and the writer assumed
  DZD when no source currency was returned. Future captures now keep the
  legacy monetary `price` column NULL and set `currency='UNKNOWN'` unless the
  extractor supplies an explicit currency. The original amount/unit/type stay
  in the observation payload. If migration 002 is installed, a parsed price
  candidate is additionally appended to `price_evidence`.

### Facebook

- `facebook_ads.url` is the ad-specific Ad Library URL. It is not the
  destination product URL.
- The destination candidate is `landing_url` in the exact
  `facebook_ad_observations.detected_metadata_json` payload. The current entity
  table's mutable fields are not used to backfill a missing historical
  destination. Worker 021 found 110 destination values across 217 observations
  and no parsed price fields.
- `facebook_ad_observations` already has exact observation IDs and timestamps;
  no price or landing-fetch table existed before this worker.

The prior schema already had append-only source observations, but it had no
versioned price interpretation, currency certainty, product-identity review,
landing-page fetch provenance, or trusted-price view. Migration 002 adds those
functions without duplicating or rewriting source observations.

## Design and implementation

### Additive schema

`migrations/002_price_evidence.sql` adds:

- `facebook_landing_page_fetches`: an append-only fetch record linked by a
  foreign key to one exact Facebook ad observation. It stores original and
  resolved URL, fetch time/status, response metadata, content hash, and
  fetcher/version provenance.
- `price_evidence`: an append-only, versioned interpretation linked either to
  one marketplace observation or one landing-page fetch. The two source paths
  have separate foreign keys and a check constraint; the landing-fetch foreign
  key leads to the Facebook observation. `source_entity_key` is descriptive;
  the exact observation/fetch foreign keys are authoritative.
- `price_evidence_reviews`: append-only decisions and any reviewed currency,
  unit interpretation, quality, price-type, and identity-key resolutions. A
  review never mutates extracted evidence.
- `trusted_price_evidence`: point-price view requiring the latest explicit
  `APPROVED` review, valid quality, successful extraction, a supported currency
  basis, a resolved unit, a keyed verified product identity, and one scalar
  amount. If the source unit was unresolved, the approved review must record
  how it was interpreted.
  It includes regular and sale prices only. Starting-from, range, installment,
  and other price types need separate analysis and are excluded from this
  point-price view.

The migration also enforces append-only behavior for fetches, evidence, and
reviews and includes uniqueness through stable idempotency keys. The helper
`apply_price_evidence_migration` is explicit and was exercised against isolated
databases; the production database was not migrated.

### Ouedkniss interpretation

`price_evidence/ouedkniss.py` preserves the source value and unit and may emit a
numeric candidate independently of monetary trust. It never derives DZD from
the Ouedkniss platform or Algerian location. `UNIT` remains unresolved. The
audited repeated `1 UNIT` pattern is marked
`SUSPICIOUS_PLACEHOLDER` with reason codes for the repeated pattern, unresolved
unit, and unconfirmed currency; it is a review flag, not a fraud finding.
Missing and malformed values map to `MISSING` and `EXTRACTION_ERROR`.

`FIXED` and `OFFERED` source labels alone do not prove that a value is a
buyer-payable amount, so those candidates retain that concern as an ambiguity
reason. No outlier threshold is used. A low value can be approved if its
currency, unit, price meaning, and product identity are supported.

The sensor writes no assumed DZD price to the legacy monetary column on future
captures. With migration 002 installed, capture appends a candidate after the
source observation is stored. A changed extractor version creates new evidence
instead of revising the previous record.

### Facebook offline extraction

`price_evidence/facebook.py` parses supplied HTML only and has no HTTP client or
browser dependency. Version `facebook_jsonld_product_offer/v1` supports:

- Product, Offer, and AggregateOffer JSON-LD;
- explicit DZD, EUR, and USD currencies and unresolved/ambiguous currencies;
- French/European decimal formatting where the number is unambiguous;
- separate sale and regular `priceSpecification` values;
- price ranges, starting-from values, and installment labels;
- multiple products and SKU variants, returning all candidates rather than
  selecting the first price;
- missing target product, inaccessible/missing content, and malformed JSON-LD
  states.

Identity remains `UNVERIFIED` or `AMBIGUOUS` unless a caller supplies keyed
identity evidence. The product key and that evidence are preserved with the candidate. The extractor
does not perform product matching or normalization. A homepage/category page
without Product JSON-LD yields no price candidate.

The tests use 13 synthetic HTML fixtures under
`tests/fixtures/price_evidence/`. No live URL was opened. Generic embedded
e-commerce JavaScript formats and visible-text price extraction are deferred:
the repository has no verified landing-page snapshots or platform-specific
formats from which to define safe selectors. Generic page-wide price regexes
would risk assigning another product's price to the ad.

## Historical provenance and trust policy

- The exact source observation timestamp is stored separately from extraction
  time. For Facebook, `source_observed_at` is the ad observation time and
  `price_observed_at`/`price_known_at` are the landing-page fetch time. A page
  fetched later is never presented as a price known at the older ad time.
- Original and resolved URLs, raw values/text, parsed candidates, unit and
  currency certainty, extraction method/version, status, reason codes, and
  supporting evidence are retained.
- Numeric parsing alone never grants trust. An explicit approved review is
  required in addition to `VALID` quality, successful extraction, supported
  currency, resolved unit, verified product identity, and a scalar regular or
  sale price.
- `trusted_price_evidence` excludes all unreviewed, rejected, or insufficiently
  supported rows. Exclusion means “not eligible for this trusted aggregate,”
  not “fraudulent.”
- A later capture or interpretation appends records. Existing source
  observations and earlier evidence are immutable.

## Isolated validation results

`data/worker_022_price_evidence_validation.sqlite` is a SQLite backup of the
configured source database with migration 002 and derived Ouedkniss evidence
applied only to the copy.

- Source row counts remained 51 marketplace observations and 217 Facebook
  observations; products, aliases, and mappings remained 0.
- 51 interpretations were appended: 16 successful numeric candidates marked
  `SUSPICIOUS_PLACEHOLDER`, and 35 `MISSING` records.
- Reprocessing the same source observations and extractor version added 0
  rows. All 16 numeric candidates preserve amount `1`, unit `UNIT`, and separate
  currency uncertainty. The trusted view returned 0 rows.
- Marketplace and Facebook source-observation fingerprints in the isolated
  database matched the production source. SQLite `integrity_check` returned
  `ok`; `foreign_key_check` returned 0 violations.
- The isolated migration test also covers append-only reviews/fetches,
  duplicate keys, new evidence versus prior evidence, review promotion, and
  trusted-view exclusions.

Full test suite: `\.venv\Scripts\python.exe -m unittest discover -s tests -q`
— 174 tests passed in 50.096 seconds, exit code 0. This includes the existing
Worker 018 normalization benchmark tests; normalization and matching logic
were not changed.

## Limitations and deferred work

- Migration 002 is not installed on production. Until a separately approved
  database migration, the updated Ouedkniss sensor safely leaves the legacy
  price column empty for unconfirmed currency and retains source data in the
  payload; the derived candidate is not stored in the production database.
- Existing historical rows that already contain assumed DZD remain as-is and
  are not trusted automatically. Any historical interpretation belongs on an
  isolated copy first, followed by explicit review.
- Facebook extraction is fixture-only JSON-LD. It does not fetch, crawl, or
  resolve live URLs and does not yet parse generic embedded JavaScript or
  visible page text.
- No product mappings, aliases, catalog rows, normalization rules, or matching
  behavior were added or changed.

