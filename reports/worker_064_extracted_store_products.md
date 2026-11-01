# Worker 064 — Extracted Store Products

**Recorded:** 2026-11-01 13:26 Asia/Riyadh
**Decision:** `BLOCKED_PENDING_OPERATOR_DECISIONS`
**Actual product-page observations:** 0

## Actual observations

No product page met the agreed `ELIGIBLE` policy after the source review. The Store Product Sensor therefore produced **no product observations** in this run. There are no observed titles, brands, models, identifiers, descriptions, categories, specifications, variants, prices, currencies, availability values, image URLs, JSON-LD fields, or field-level provenance to report. No product price was requested, copied, trusted, or approved.

| Outcome | Count |
|---|---:|
| Product-page route candidates in the register | 8 |
| Eligible product URLs | 0 |
| Product-page HTTP attempts | 0 |
| Successful HTTP responses | 0 |
| Failed HTTP responses | 0 |
| Successful extractions | 0 |
| Incomplete extractions | 0 |
| Failed extractions | 0 |
| Not attempted: source review required | 6 |
| Not attempted: excluded by terms or transport | 2 |
| Non-product landing routes not requested | 1 |

The HTTP and extraction outcomes are separate: no product-page HTTP request occurred, and extraction was not run. The zero failure count means **no request was made**, not that any page was reachable. The machine-readable file contains the same empty observation set and per-route outcomes: [worker_064_extracted_store_products.json](worker_064_extracted_store_products.json).

## Candidates not attempted

### Kingdom SHOP — `KW063-M01` / `KW063-U01`

- **Domain / operator:** `kingdomshop.shop`; legal operator unknown.
- **Source Ad Library evidence:** Library ID `4200798420153598`, stored ad record 10, stored observation 76, `2026-10-06T16:30:50.866027Z`, `DZ / Peinture`. [Ad](https://www.facebook.com/ads/library/?id=4200798420153598)
- **Product-page route candidate:** [URL](https://kingdomshop.shop/products/%F0%9F%94%A7-compresseur-d-air-silencieux-9l-dingqi-puissance-silence-et-mobilite); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`. Current terms/legal evidence and legal operator were not established; the merchant/operator link remains uncertain.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### peinture pro — `KW063-M02` / `KW063-U02`

- **Domain / operator:** `coolay.justsell.store`; legal operator unknown.
- **Source Ad Library evidence:** Library ID `2270290663891071`, stored ad record 12, stored observation 101, `2026-10-06T16:30:50.866027Z`, `DZ / Peinture`. [Ad](https://www.facebook.com/ads/library/?id=2270290663891071)
- **Product-page route candidate:** [URL](https://coolay.justsell.store/product/22865); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`. The advertiser/store identity could not be matched confidently to the hosted domain, the legal operator is unknown, and current terms were not established.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### Bricoutillages — `KW063-M03` / `KW063-U03`

- **Domain / operator:** `bricoutillages.com`; public store identity and its Algerian location are stated on the site; legal operator not linked conclusively to the reviewed pages.
- **Source Ad Library evidence:** Library ID `1858565942163492`, stored ad record 70, stored observation 109, `2026-10-06T16:30:50.866027Z`, `DZ / Peinture`. [Ad](https://www.facebook.com/ads/library/?id=1858565942163492)
- **Product-page route candidate:** [URL](https://bricoutillages.com/product/tronconneuse-meuleuse-1010w-115mm-ct13022); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`. The current [privacy notice](https://bricoutillages.com/confidentialite/) describes personal-data use but does not identify the legal operator. The [homepage/contact information](https://bricoutillages.com/contact/) identifies the public-facing store and its Draria address. The store references sales conditions at purchase; their text and relevance to this limited extraction were not resolved. Its [robots.txt](https://bricoutillages.com/robots.txt), reviewed 2026-11-01, returned HTTP 200 and does not disallow the `/product/` route. That crawl rule is not legal authorization.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### Vendex DZ — `KW063-M04` / `KW063-U04`

- **Domain / operator:** `vendex-dz.com`; legal operator not established.
- **Source Ad Library evidence:** Library ID `1086235407490463`, stored ad record 151, stored observation 206, `2026-10-06T16:30:50.866027Z`, `DZ / Peinture`. [Ad](https://www.facebook.com/ads/library/?id=1086235407490463)
- **Product-page route candidate:** [URL](https://vendex-dz.com/product/bache-de-protection-moto-impermeable-anti-uv-poussiere-et-vent/); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`. The [terms](https://vendex-dz.com/terms/), updated 2026-04-07, state that site content may not be copied or republished without prior written permission. Whether the proposed limited extraction falls within that restriction is materially ambiguous. Operator identity is also unresolved.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### Ooredoo Algérie — `KW063-M05` / `KW063-U05`

- **Domain / operator:** `www.ooredoo.dz`; legal operator established as Wataniya Télécom Algérie SPA.
- **Source Ad Library evidence:** Library ID `1896721121696626`, `smartphone` query, ad start date `2026-10-22`. [Ad](https://www.facebook.com/ads/library/?id=1896721121696626)
- **Product-page route candidate:** [URL](https://www.ooredoo.dz/ar/p/telephone-et-autres/store/samsung-a17-5g); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`, as required by the pilot's special case. The [legal notice](https://www.ooredoo.dz/fr/particuliers/legal-notice) and robots file do not resolve the scope of use/reproduction for this collection. The robots file's empty `Disallow` is not authorization.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### Casa Chic Algerie — `KW063-M06` / `KW063-U06`

- **Domain / operator:** `casachicalgerie.com`; legal operator unknown.
- **Source Ad Library evidence:** Library IDs `1508900221277642` (start date `2026-09-14`) and `28183075348011287` (start date `2026-09-10`), `meuble algerie` query. [Ad 1](https://www.facebook.com/ads/library/?id=1508900221277642), [Ad 2](https://www.facebook.com/ads/library/?id=28183075348011287)
- **Product-page route candidate:** [URL](http://casachicalgerie.com/product/meuble-a-chaussures-2-3-portes/).
- **Eligibility:** Source `REVIEW_REQUIRED`; this URL is `EXCLUDED` from the pilot because it is HTTP/port 80, while the transport limit is HTTPS on port 443. Worker 063 could not resolve the [terms](https://casachicalgerie.com/terms-and-condition/) or [robots file](https://casachicalgerie.com/robots.txt); no HTTPS equivalent was assumed.
- **HTTP outcome / observation time:** `NOT_REQUESTED_TRANSPORT_EXCLUDED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### UniQa Dz — `KW063-M07` / `KW063-U07`

- **Domain / operator:** `dzuniqa.shop`; legal operator unknown.
- **Source Ad Library evidence:** Library ID `1821150515694380`, `vetement algerie` query, ad start date `2026-10-24`. [Ad](https://www.facebook.com/ads/library/?id=1821150515694380)
- **Product-page route candidate:** [URL](https://dzuniqa.shop/products/trench2min); page type is based on route shape, not live page verification.
- **Eligibility:** `REVIEW_REQUIRED`. Current terms/legal evidence, a robots review, and the legal operator were not established.
- **HTTP outcome / observation time:** `NOT_REQUESTED_REVIEW_REQUIRED` / `null`.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

### Diar Dzair — `KW063-M08` / supplemental route `KW064-U08`

- **Domain / operator:** `alsalam.diardzair.com.dz`; the linked platform terms identify “Diar Dzair, e-commerce et innovation” and Algerian commercial registration `16/00-1013565B18`.
- **Source Ad Library evidence:** Library ID `1494712169083330`, `electromenager` query, ad start date `2026-09-13`. [Ad](https://www.facebook.com/ads/library/?id=1494712169083330)
- **Original Ad Library landing URL:** [category/search route](https://alsalam.diardzair.com.dz/search/?page=1&parent=Electromenager); it is not a product page.
- **Supplemental route candidate:** [URL](https://alsalam.diardzair.com.dz/product/6c311902358a21ca00f506434f58014f), surfaced from the public catalogue/search evidence. It was not the ad landing URL and no product facts were used from the search result.
- **Eligibility:** `EXCLUDED`. The platform's [conditions of use](https://new.diardzair.com.dz/conditionsgenerales), linked in the site's public navigation and reviewed 2026-11-01, restrict reproducing, transferring, modifying, redistributing, retransmitting, publishing, or commercially exploiting site data without prior written authorization. No such authorization was supplied.
- **HTTP outcome / observation time:** `NOT_REQUESTED_TERMS_EXCLUDED` / `null` for the supplemental route; the category/search route was also not requested.
- **Extraction:** `NOT_RUN`; all product fields, specifications, variants, price, currency, availability, provenance, and conflicts are unknown.

## Separately preserved exclusion

**E-Sahel (`www.esahel.com`) remains `EXCLUDED`** under the written-authorization condition recorded in Worker 057/061. No documented authorization was supplied and no product URL was requested.

## Provenance and storage

There are no field-level product provenance entries because the Store Product Sensor processed zero product pages. Ad Library evidence remains linked per candidate above. No raw HTML was retained, no research observation database was activated, and no trusted price evidence was written. The research store's explicit retention period and approval reference remain unset; the required Markdown and JSON reports carry this run's audit trail.
