# Sensor Integration Status for Foundation V2

The Ouedkniss sensor migration and seller identity hardening are implemented.
The Facebook section below remains an inspection of that separate sensor; the
Facebook sensor was not changed.

## Ouedkniss sensor — Foundation V2 migration

`sensors/ouedkniss_sensor.py` opens the configured database through
`config.connect()`. It resolves sellers first by `(platform,
source_seller_id)`. If that stable ID is not present, it checks exact incoming
listing IDs and generated canonical listing URLs against existing Ouedkniss
listings. One unambiguous match to a seller with a null source ID adopts that
seller row, adds verified metadata, and merges the new data into its existing
`source_payload_json`, retaining legacy migration provenance. No match creates
a seller. Conflicting stable owners or multiple legacy sellers cause the store
to be logged and skipped. Display names and wilayas are never used as identity
evidence. The adoption evidence is recorded in seller payload JSON.

The sensor upserts listings by `(platform, source_listing_id)`, leaves
`product_id` null when creating a listing, and appends an observation for each
successful capture. First-seen times are preserved, last-seen times are
updated, and optional source fields are retained when later responses omit
them.

The raw title is stored unchanged. The sensor does not create canonical
products, normalize titles, or infer product mappings. A listing present in the
current promoted response is recorded as `active` with availability `listed`.
An omitted listing is never marked inactive or deleted because this response is
partial.

### Verified fields and price

The original `FrontStoreQuery` selection returned only `id`, `title`, `slug`,
`defaultMedia`, and `__typename`. The current Ouedkniss frontend includes an
`Announcement` fragment with the exact fields `price`, `priceUnit`, `priceType`,
and `status`. Adding those verified fields to the existing query succeeded in a
read-only live request; GraphQL schema introspection remains disabled.

The earlier read-only response contained 17 announcements from 3 promoted
stores. Eight had numeric prices, each with `priceUnit=UNIT`; the other nine
returned a null price. Worker 022 changed future handling: without source
currency, the legacy observation's monetary `price` is left `NULL` and its
currency is `UNKNOWN`. The exact amount, unit, and type remain in the immutable
source payload. When migration 002 is installed, the sensor also appends a
versioned price-evidence candidate linked to that exact observation. `UNIT`
stays unresolved and no DZD value is inferred. Older rows that already contain
the documented DZD assumption are not rewritten.

The capture entry point now checks the configured database read-only before
fetching and checks again before database writes. Missing or structurally
incomplete Migration 002 or Migration 003 produces a clear error and stops capture; the sensor
does not apply migrations. Read-only migration status remains available when
price evidence is absent. See `README.md` for preflight, apply, and verified
restore commands using an explicitly selected isolated database.

`PUBLISHED` maps to `active`; other source status values map to `unknown` until
their meanings are verified. Availability stays separate as `listed`, since the
announcement was present in this response. Unit tests cover a numeric UNIT
price, a MILLION-unit conversion, a null source price, and unknown statuses.

### Capture scope and history

The existing query calls `promotionFetch` for `CROSS_HOME_FRONT_STORE` with
`maxCount=16`, then requests page 1 of up to 8 image-bearing announcements for
each returned store. This is a limited promoted-store sample, not a full
Ouedkniss marketplace crawl. Every capture gets a UTC ISO-8601 timestamp.
Exact same-time observation duplicates are ignored with a warning; prior
observations remain unchanged. Seller, listing, and observation writes share a
single transaction, so a database failure rolls back the capture.

Source payloads retain seller and listing IDs, slugs, region, title, media,
capture scope, timestamp, raw price, and source unit. If a slug is missing,
the sensor does not fabricate a URL; it preserves an existing URL or leaves a
new listing URL null.

Regression tests use an isolated temporary V2 database. The repair was made
in a single transaction. A read-only live `FrontStoreQuery` probe then saw
`36593` / LALMI CAR → seller 1, `37025` / SARL ILTOCCO ITALIANO → seller 2,
and `27943` / BATI ASRI Promotion → seller 3 by stable source ID. Database
counts did not change during the probe, and no live sensor capture was run.
The repository test suite passed all 28 tests.

## Facebook Ad Library sensor

**Audited starting point.** The original `sensors/facebook_ads_sensor.py`
launched a headless Playwright Chromium browser and searched Facebook Ad Library
for keyword `Peinture`, country `DZ`, all ad statuses/types and all media. It
scrolled four times, then scanned DOM text for `Library ID` / `رقم تعريف
المكتبة`, a displayed start date and Facebook/Instagram mentions. It did not
extract page identity, creative, landing URL or media. It treated a missing
`Active` label as `paused`, created a dummy legacy `Products` row, and inserted
new records into legacy `Advertising_Campaigns`; it had no observation history.

**V2 sensor.** The rewritten sensor uses the same Playwright Ad Library source
and query, with bounded scrolling that stops after two scrolls add no unique
ads or after 12 scrolls. It examines the currently rendered DOM, records the
visible Library ID, ad-specific Library URL, page-link labels, platform labels,
displayed status/date, images, videos, external links, and raw card payload when
the DOM exposes them. Fields that are absent or cannot be parsed stay null; the
raw visible card text and link/media evidence remain in the payload. Creative
text is left null unless a source field can be established reliably. The live
read-only validation below records which fields were actually present in the
current response. The browser scan is a bounded sample, not a complete archive
export.

Canonical identity uses the Library ad ID shown in the card or in an ad URL's
`id`, `ad_id`, or `library_id` query parameter. It resolves directly by
`(platform='facebook', library_id)`. Only when no stable ID exists does the
sensor use a normalized ad-specific Ad Library URL, with tracking parameters
removed. It does not use page name, creative text, dates, or result position as
identity. A card with neither identity is skipped and logged.

Each distinct ad in one capture is upserted in `facebook_ads`; a later capture
reuses that row and appends one `facebook_ad_observations` record. Duplicate
input cards are deduplicated within a capture. Observation payloads retain the
raw source card, normalized fields, query keyword/country, identity method,
source ID, source URL, and observation-time metadata. Existing observations are
immutable per schema triggers. No canonical products are created or linked,
and legacy Facebook tables are not written.

Status is `active` or `inactive` only when an explicit status label is observed.
An ad appearing in a crawl is not evidence that it is active. Missing or
unrecognized status maps to V2 `unknown` and observation `active=NULL`.
Displayed start dates are parsed only for supported formats; unparsed text is
retained and the date column stays null. Nullable scalar fields on the
canonical ad retain their last known values when a later response omits them;
that capture's observation records the missing value as null. Because
`platforms_json` is a required JSON array in V2, no observed placement labels
are represented as an empty array.

All canonical and observation writes occur in one SQLite transaction using
`config.connect()`. Database and integrity errors propagate after rollback;
they are not swallowed. The default command runs read-only validation. The
explicit `--capture` command is the only live database write path and must be
used only for an authorized controlled capture.

**Configured database before the Facebook work.** Read-only inspection of
`data/Database.sqlite` found 69 migrated `facebook_ads`, 69 baseline
`facebook_ad_observations`, and 69 rows in the untouched
`legacy_advertising_campaigns`; no live Facebook write was made during this
inspection. All 69 migrated ads have `library_id=NULL`, but their existing
canonical URLs contain `?id=<Library ID>`. On the first capture, a matching
stable ID therefore resolves to that existing row by canonical URL and fills
its `library_id`; it does not create a second ad. A regression test covers this
upgrade path. The older hard-coded DataGrip database path still contains the
pre-V2 schema and is not used by the rewritten sensor.

**Read-only live validation (2026-10-06).** After the complete test suite
passed, the Ad Library page was opened with the existing `Peinture` / Algeria
query and inspected in the in-app browser. The visible page showed
`~22,000 results`, sorted by total impressions descending. It redirected to
`web.facebook.com` and added `is_targeted_country=false` and the sort settings
to the URL. The DOM exposed 168 distinct Library IDs after 12 bounded scrolls;
there were no repeated IDs among those rendered cards. Scrolling loaded more
cards throughout the sample, so it did not establish full result coverage.
There was no search-results next-page control. Visible `Previous items` /
`Next items` controls belonged to individual ad media carousels.

Fields observed in those 168 cards:

| Field | Observed |
|---|---:|
| Numeric `Library ID` | 168 / 168 |
| Explicit status label | 168: 18 Active, 150 Inactive |
| `Started running on` date | 18 |
| Displayed date range | 144 |
| Advertiser/page name | 168 |
| Page source ID in an advertiser link | 1 |
| Image media | 167 |
| Video media | 87 |
| Decoded/direct destination link | 126 |
| Creative text in a card button | 168 |
| Explicit placement label | 0 |
| Currency-like text in creative copy | 17 |

The visible identity field is the card label `Library ID: <digits>`; the cards
did not expose a structured JSON schema or an ad-specific link in the DOM, so
the sensor derives the standard `/ads/library/?id=<Library ID>` URL when that
link is absent. Active/Inactive are explicit labels on this response. A date
range is not treated as a start date; it remains in the raw card payload. The
page exposed a generic `Platforms` affordance but no placement values, so the
sensor leaves placements empty instead of guessing. Currency-like values were
found in ad text, not as structured price fields; the sensor keeps them only
as creative/raw text and does not parse them into a price or product.

This was a browser-only read. The sensor database writer was not called. A
separate final read-only SQLite check still showed sellers=3,
marketplace_listings=17, marketplace_listing_observations=51, products=0,
facebook_ads=69, facebook_ad_observations=69, and
legacy_advertising_campaigns=69; integrity was `ok` and foreign-key checks
returned no violations.

**First controlled live write — required steps after CEO authorization.**

1. Use this V2 database: `data/Database.sqlite` (or set
   `KRATZWERK_DB_PATH` to that file). Do not point the sensor at the older
   DataGrip database, which still has the pre-V2 schema.
2. Make the Playwright Python package and Chromium available in the execution
   environment. Playwright is not installed in the environment used for this
   read-only validation.
3. Rerun `python -m unittest discover -s tests -v` and confirm all tests pass.
4. Record the V2/legacy row counts and integrity checks immediately before
   capture.
5. Run exactly one capture with
   `python -m sensors.facebook_ads_sensor --capture --keyword Peinture --country DZ`.
6. Read the tables back and confirm each unique returned ad has one canonical
   row and one new observation, products remains 0, legacy Facebook counts did
   not change, and SQLite integrity/foreign-key checks pass. Do not infer
   absence from this bounded sample or run another capture as part of this
   first-write gate.

## Integration gate

The Ouedkniss sensor now uses `config.connect()`, stable seller IDs with
evidence-based legacy seller adoption, verified price and status fields,
nullable product links, and append-only listing observations. Product
normalization and intelligence remain out of scope. The promoted-store sample
is not a complete marketplace crawl; any future expansion needs a separate
coverage design before absence can imply deletion. The Facebook sensor
migration remains a separate task.
