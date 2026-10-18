# Worker 036 — Store Product Extraction Sensor

## Architecture decision

Added `sensors/store_product_sensor.py` as a pure parser for HTML already retrieved by a caller-approved fetcher. It returns independent `ProductRecord` values and makes no network or database calls. This is the safest integration point: the existing fetches are source-specific, while the existing schema has no neutral store-page extraction observation table. The parser therefore does not write extracted facts into mutable product text, create canonical product identities or mappings, or feed trusted price evidence.

The parser reuses the repository's Python standard-library conventions (`HTMLParser`, JSON parsing, timezone-aware UTC timestamps) and the project's existing distinction between source observations and canonical products. Product and Offer JSON-LD is preferred; explicit product metadata is next. A conservative metadata-gated `h1` fallback is available when the page is explicitly marked as a product. Every extracted field includes its markup method and source path. Conflicting names are left null in `fields` and the candidate values are retained under `unresolved` with all provenance entries. Offers stay as separate price/currency/availability tuples; JSON-LD variant Products are returned as independent records.

## Fields and limits

Supported fields include product name, brand, model, SKU, MPN, GTIN, description, product and canonical URLs, images, explicit product options/variants, and offers with price, currency, availability, offer URL, and offer SKU. Source URL, UTC retrieval time, and extraction method are always recorded on returned records.

This MVP retains explicitly represented values as published. It does not normalize currencies or availability, infer absent facts, or reconcile distinct offers. JSON-LD `ProductGroup` itself is not treated as a product; its typed `Product` variants are. It does not implement generic microdata/RDFa traversal or broad selector heuristics. Malformed JSON-LD blocks are skipped. Extraction is deterministic for fixed HTML, source URL, and timestamp.

## Persistence and safety

Persistence is intentionally deferred. Migrations 001–004 contain no neutral store-page extraction observation table, and no migration was added. No canonical product or mapping is written, and no `trusted_price_evidence` workflow is called. No production migration, production capture, or production database write was performed.

No network fetcher was implemented. Thus redirect checks, DNS/IP destination checks (including localhost, private, and link-local addresses), response-size limits, and request timeouts remain responsibilities of a future approved fetch integration. The parser rejects non-HTTP(S), credential-bearing, and malformed source URLs but does not resolve destinations. No crawl or live network test was run.

## Focused verification

Command: `\.venv\Scripts\python.exe -m unittest tests.test_store_product_sensor -v`

Result: 7 tests passed. Fixtures cover Shopify-like product metadata, custom-store metadata, JSON-LD variants, multiple offers and currencies, conflicting title evidence, malformed JSON-LD, category/non-product pages, URL scheme rejection, provenance, and deterministic repeated extraction. The test confirms returned extraction payloads do not contain a trusted-price evidence reference. No full suite was run.

## Files changed

- `sensors/store_product_sensor.py`
- `tests/test_store_product_sensor.py`
- `tests/fixtures/store_products/shopify_like.html`
- `tests/fixtures/store_products/jsonld_variants.html`
- `tests/fixtures/store_products/custom_store.html`
- `tests/fixtures/store_products/conflicting.html`
- `tests/fixtures/store_products/category.html`
- `reports/worker_036_store_product_sensor.md`
- `reports/worker_036_results.json`

## Database integrity

The protected production database is `data/Database.sqlite`. Its current SHA-256 is `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`, matching the prior Worker 035 recorded before/after hash. This task made no production database writes or migrations.
