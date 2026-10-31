# Worker 062 — Product URL Register

**Recorded:** 2026-10-31 20:49 Asia/Riyadh

**Merchant product-page requests:** 0

## Candidate routes

Each URL below is copied from the stored `landing_url` field in a specific Ad Library observation. Classification is based on the stored URL structure only. No merchant destination was requested, so none is a verified live product page. The first four URLs are linked to provisional public-facing store records. The San Marco route is retained as an observed product-route lead but is not counted as an Algerian merchant candidate.

| URL ID | Merchant candidate | Stored destination URL | Classification | Product title/category evidence | Source ad and discovery provenance | Eligibility |
|---|---|---|---|---|---|---|
| `KW062-U01` | `KW062-M01` — Kingdom SHOP | https://kingdomshop.shop/products/%F0%9F%94%A7-compresseur-d-air-silencieux-9l-dingqi-puissance-silence-et-mobilite | `PRODUCT_PAGE_CANDIDATE` | URL slug contains French product wording; stored creative is the `Open Dropdown` placeholder. Title/category not verified. | Observation `76`; `facebook_ads.id=10`; Library ID `4200798420153598`; `page_source_id=NULL`; Sensor query `DZ` / `Peinture`. [Ad](https://www.facebook.com/ads/library/?id=4200798420153598) | `REVIEW_REQUIRED` — destination not live-verified; source terms, robots, legal basis, operator, and access status unresolved. |
| `KW062-U02` | `KW062-M02` — peinture pro | https://coolay.justsell.store/product/22865 | `PRODUCT_PAGE_CANDIDATE` | Numeric product route only; title/category unavailable. | Observation `101`; `facebook_ads.id=12`; Library ID `2270290663891071`; `page_source_id=NULL`; Sensor query `DZ` / `Peinture`. [Ad](https://www.facebook.com/ads/library/?id=2270290663891071) | `REVIEW_REQUIRED` — destination and operator not live-verified; source terms, robots, legal basis, and access status unresolved. |
| `KW062-U03` | `KW062-M03` — Bricoutillages | https://bricoutillages.com/product/tronconneuse-meuleuse-1010w-115mm-ct13022 | `PRODUCT_PAGE_CANDIDATE` | Slug suggests tool wording, but no stored product title or category field confirms it. | Observation `109`; `facebook_ads.id=70`; Library ID `1858565942163492`; `page_source_id=NULL`; Sensor query `DZ` / `Peinture`. [Ad](https://www.facebook.com/ads/library/?id=1858565942163492) | `REVIEW_REQUIRED` — destination and Algerian operator status not live-verified; source terms, robots, legal basis, and access status unresolved. |
| `KW062-U04` | `KW062-M04` — Vendex DZ | https://vendex-dz.com/product/bache-de-protection-moto-impermeable-anti-uv-poussiere-et-vent/ | `PRODUCT_PAGE_CANDIDATE` | Slug suggests motorcycle-cover wording, not a stored product title or category field. | Observation `206`; `facebook_ads.id=151`; Library ID `1086235407490463`; `page_source_id=NULL`; Sensor query `DZ` / `Peinture`. [Ad](https://www.facebook.com/ads/library/?id=1086235407490463) | `REVIEW_REQUIRED` — destination not live-verified; legal operator, terms, robots, legal basis, and current access unresolved. |
| `KW062-U05` | No merchant candidate assigned — San Marco brand route | https://fr.san-marco.com/product/20238184/antartica | `PRODUCT_PAGE_CANDIDATE` | `antartica` appears in the route; no stored product title/category confirms page contents. | Observation `212`; `facebook_ads.id=157`; Library ID `2617245195313634`; `page_source_id=NULL`; Sensor query `DZ` / `Peinture`. [Ad](https://www.facebook.com/ads/library/?id=2617245195313634) | `INSUFFICIENT_EVIDENCE` — route is associated with a brand, but evidence does not establish an Algerian merchant operator or a distinct store business. Not counted in the merchant candidate set. |

None of the five URLs includes query parameters. No query value was removed or invented. The destination URLs were not resolved, redirected, or otherwise inspected over the network. The `PRODUCT_PAGE_CANDIDATE` label means only that the stored path uses a product route pattern; it does not verify a product page's current contents or availability.

## Offline classification totals for stored destinations

The 110 non-empty `landing_url` observation values collapse to 63 distinct URLs. A path/host-only classification of all 63 produced:

| Classification | Distinct URLs |
|---|---:|
| `PRODUCT_PAGE_CANDIDATE` | 5 |
| `STORE_HOME_PAGE` | 6 |
| `CATEGORY_PAGE` | 2 |
| `SEARCH_OR_LISTING_PAGE` | 10 |
| `SOCIAL_OR_ADVERTISEMENT_PAGE` | 33 |
| `REDIRECT_OR_UNKNOWN` | 7 |
| `INVALID_OR_UNSUPPORTED` | 0 |
| **Total** | **63** |

These are preliminary offline route classifications, not live destination verification. The 58 non-product-route URLs are counted above but not reproduced here because they are outside this product candidate register. Their existence does not make them merchant product-page candidates.

## Eligibility totals for the five product-route candidates

| Eligibility status | URLs |
|---|---:|
| `ELIGIBLE_FOR_FINAL_REVIEW` | 0 |
| `REVIEW_REQUIRED` | 4 |
| `EXCLUDED` | 0 |
| `INSUFFICIENT_EVIDENCE` | 1 |

E-Sahel remains separately `EXCLUDED` under the prior Worker 061 terms review unless the recorded written-authorization requirement is documented as satisfied. It was not present among these five stored product-route URLs.
