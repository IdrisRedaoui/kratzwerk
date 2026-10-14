# Worker 021 — Read-Only Price Integrity Audit

Run date: 2026-10-11 (Asia/Riyadh)

## Scope

This audit read the current SQLite schema, sensor/parser source, and stored observations. It made no schema change, price correction, production write, provider call, or landing-page request. Facebook destinations below were inspected as stored URLs only; no destination was opened or fetched.

## C1. Ouedkniss prices

### Facts observed

- The current schema stores listing prices on marketplace_listing_observations: numeric price, non-null currency, listing status, availability, source payload, and observed_at. The row key identifies the observation; listing_id links it to the marketplace listing.
- The FrontStoreQuery selects price, priceUnit, priceType, and status. It does not select a currency field.
- The parser accepts a finite, nonnegative numeric amount, multiplies it by its recognized source priceUnit, and returns no currency. UNIT has multiplier 1. The writer then stores DZD as the default currency and labels currency_basis as assumed_from_Algerian_marketplace. The source amount, source unit, and price type remain in the observation JSON as announcement.price, announcement.price_unit, and announcement.price_type. The JSON also records the extraction method as FrontStoreQuery.price. It does not record a price-extraction version.
- Of 51 current listing observations, 16 have a numeric price and 35 have NULL. All 16 numeric values are 1.0 in the price column; their source payloads say price=1 and price_unit=UNIT. They cover 8 listing IDs over two capture timestamps (8 observations per capture). Six are labeled FIXED and ten OFFERED. The stored currency is DZD, but each of these 16 payloads labels that currency as assumed.
- There is no independent price-quality status or reason in the current schema. The status column is the listing status, not a judgment about price validity.

### Interpretation

The observed record is structured source data containing a numeric 1 and the declared unit UNIT. The application turns that into amount 1.0 and assumed currency DZD. UNIT is the parser's amount-scale value; by itself it does not confirm DZD, a retail price, or that the value is payable. FIXED/OFFERED also does not establish that the amount is a genuine monetary offer.

The repeated 1 UNIT value across 8 listings and two captures is a strong placeholder warning, but the stored evidence does not prove that the values are fake. No price should be rewritten or removed. For trusted aggregates, exclude these rows until an explicit quality review confirms the amount, unit, and currency; retain the source payload and numeric observation unchanged.

### Facts, inferences, and unknowns

| Kind | Finding |
| --- | --- |
| Fact | The source payload records 1, UNIT, and a FIXED or OFFERED price type; the observation stores 1.0 and DZD with currency basis marked assumed. |
| Fact | The parser treats UNIT as a multiplier of one and has no source currency input. |
| Inference | The uniform 1 UNIT pattern is likely a placeholder or incomplete offer, so it should not enter trusted market-price aggregates yet. |
| Unknown | Whether a seller intended a genuine 1 DZD offer, whether UNIT denotes a per-item amount or a placeholder convention, and which currency the source meant. |
| Unknown | Whether priceType confirms that the displayed numeric field is a buyer-payable price. |

### Future data and quality policy

Keep each source observation and append any derived price interpretation. A future price-observation record should preserve:

- the raw source value and unit (including raw text when the source provides text), plus parsed amount only when defensible;
- currency code and whether it is source-confirmed, independently verified, or unknown;
- source platform, entity ID, exact source-observation ID, and source URL;
- source observation time separately from extraction/processing time;
- extractor name and version, evidence pointer or content hash, and parse/review reason;
- a price-quality status independent of listing/ad status.

Suggested quality statuses are VALID, SUSPICIOUS_PLACEHOLDER, UNIT_OR_CURRENCY_UNCONFIRMED, MISSING, AMBIGUOUS, and EXTRACTION_ERROR. Keep the selected status and one or more review reasons separate so, for example, a suspicious amount can also retain an unconfirmed currency reason.

Trusted price aggregates should include only rows with VALID evidence under an explicit policy. Keep all non-valid rows, source payloads, and later review decisions for audit. A statistical outlier should trigger review, not deletion or automatic invalidation. Validate comparisons by equivalent product/model, package size, unit, currency, and observation period. A genuinely low price may be a real undercut, promotion, or price dumping; the price alone does not prove bad data.

## C2. Facebook Ads and destination prices

### Current schema and fields

The facebook_ads table has a url column for the canonical, ad-specific Meta Ad Library URL, alongside the Library ID and mutable ad/entity fields. It has no landing_url or price column. The separate facebook_ad_observations table has observed_at and detected_metadata_json.

In the current database, all 162 facebook_ads rows have an Ad Library URL. The observation JSON has its own url field in 148 of 217 rows and a landing_url field in 110 of 217 observations (63 distinct landing URL values). The destination value is also present in the current entity payload for 110 ads. No current entity or observation payload has structured keys for price, sale_price, offers, or AggregateOffer. Creative text is not a parsed price field.

The sensor extracts an ad-specific Ad Library link or derives one from the Library ID. Separately, it looks for an l.facebook.com redirect parameter or a non-Facebook link on the rendered ad card and stores that candidate as landing_url. It does not fetch the destination, follow its redirects, verify a product page, or extract a price. Stored destination links include social/app, short/redirect, and other non-product destinations; a non-Facebook URL is not proof of a product detail page. The ad-details URL and destination URL must stay separate.

### Recommended bounded extraction flow

1. Use reliable product and offer structured data first: JSON-LD Product, Offer, and AggregateOffer, with explicit price, priceCurrency, SKU/variant, availability, and offer type when available.
2. If that is absent, inspect public embedded e-commerce product data exposed by the page.
3. If structured data is unavailable, read only the rendered, visible product-page price.

Before attaching a price, check product identity against the ad evidence. Prefer an exact model, SKU, GTIN, brand/product name, or a corroborated combination of title and image. A homepage, category page, unrelated offer, multi-product page, or weakly similar title should return ambiguous or identity-mismatch status rather than a price mapped to the advertised item.

For each request, retain the original ad destination, redirect chain, final response URL, and page canonical URL as distinct values. Do not replace the original evidence with a redirect target or canonical URL. Record sale price and regular price as separate offer roles. Preserve ranges as lower and upper amounts and retain a starting-from flag; do not collapse them to a single exact price. Store variants/SKUs separately when they have distinct prices.

Require an explicit currency from structured data or clear page context; do not infer it from the page host, country, IP location, or the ad's market. Parse localized separators only when locale/currency context is clear; ambiguous formats stay AMBIGUOUS. Record unavailable, blocked, price-free, inaccessible, and product-mismatch outcomes without substituting zero or another product's price.

For a bounded future job, use at most one public destination-page request for each ad observation in one extraction run, deduplicate requests by safe canonical page URL, cap redirects at five, use a short connection/overall timeout (for example 5/15 seconds), and cap response bodies (for example 5 MiB). Apply a per-host rate limit. Fetch public pages only: no login, account creation, cart, checkout, payment, purchase, or other authenticated/action flow. Store the fetch time, request outcome, extractor version, evidence fields, and content hash so another run can explain what it saw.

Possible outcomes include VALID, SUSPICIOUS_PLACEHOLDER, UNIT_OR_CURRENCY_UNCONFIRMED, MISSING, AMBIGUOUS, EXTRACTION_ERROR, BLOCKED, UNAVAILABLE, NO_PRICE, and PRODUCT_IDENTITY_MISMATCH. These are proposals only; no landing pages were fetched in Worker 021.

## C3. Temporal and schema integrity

A price observed on a landing page today is evidence for the page at today's fetch time. It is not evidence of the price when an older Facebook ad observation was captured. Keep the ad observation time, landing-page fetch time, and any page-declared offer validity window as separate timestamps.

A future append-only price-observation model should identify the exact evidence source with source platform, source entity ID, source observation ID, and a fetch/extraction run ID. It should retain requested, redirected, and canonical page URLs; product/variant/offer identity when verified; amount or range; currency and confidence; sale/regular role; availability; quality status/reason; observation and fetch times; extraction method/version; and source evidence hash or structured-data excerpt reference. Leave product identity nullable when it is not established.

Use an idempotency key tied to the source observation, destination/page identity, extraction run or fetch time, offer/variant key, and extractor version. Permit later fetches to append new snapshots rather than update the prior record. This supports page-price history and market comparisons while preserving when each value was actually observed. Historical analyses should compare price observations by their own timestamps and should never relabel a current page price with an older ad's timestamp.

## Facts, inferences, and unknowns

### Facts

- The Ouedkniss numeric price field contains 16 values, all 1.0; the source payloads contain price 1 and unit UNIT.
- The current Ouedkniss writer assumes DZD when the source response does not provide a currency.
- Facebook currently separates its Ad Library URL column from landing_url evidence stored in JSON; it has no parsed price column or structured offer-price fields.
- Worker 021 did not make any external requests or modify pricing data.

### Inferences

- The repeated Ouedkniss value is suspicious enough to quarantine from trusted aggregates pending review, but not enough to erase or declare false.
- The current Facebook destination URLs can seed a future review/fetch queue, but they cannot by themselves identify the advertised product or establish a current price.

### Unknowns

- Whether the Ouedkniss 1 UNIT values are real prices, placeholders, incomplete offers, or use a source convention not represented in the current schema.
- Whether DZD is correct for each Ouedkniss amount; it is an application assumption, not a source currency field.
- Whether each Facebook landing_url still resolves, whether it leads to a product detail page, and whether its current page price corresponds to the advertised product.
- Historical Facebook landing-page prices at the time of older ad observations; no such page snapshots are present in the current data.
