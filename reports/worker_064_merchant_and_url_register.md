# Worker 064 — Merchant and URL Register

**Reviewed:** 2026-11-01 (Asia/Riyadh)
**Decision:** `BLOCKED_PENDING_OPERATOR_DECISIONS`

## Register summary

- Eight Ad-derived merchant candidates from Worker 063 were reassessed; no additional merchant candidate met the route and identity threshold during expansion.
- Two legal operator identities are evidenced: Wataniya Télécom Algérie SPA for Ooredoo and Diar Dzair, e-commerce et innovation for Diar Dzair. The legal-operator distinctness of the remaining brands is unresolved; the eight brands/domains are not treated as eight proven separate operators.
- Seven product-route candidates from Worker 063 were retained. One additional Diar Dzair product-route candidate was surfaced on the same domain family during current public-site review. There are **eight** product-route candidates total.
- **Eligible:** 0 merchants / 0 product URLs. **Review required:** 7 merchants / 6 product URLs. **Excluded:** 1 merchant by site terms (Diar Dzair) / 2 product URLs (Diar Dzair terms and Casa Chic HTTP). E-Sahel remains separately excluded.
- No product-page request was made. No URL was treated as eligible merely because it is public, and written merchant permission was not imposed as a blanket prerequisite.

## Candidate register

| Candidate | Public brand/store | Domain | Legal operator status | Ad Library provenance | Product route(s) | Source / URL outcome |
|---|---|---|---|---|---|---|
| `KW063-M01` | Kingdom SHOP | `kingdomshop.shop` | Unknown | Library ID `4200798420153598`; stored ad 10 / observation 76; `DZ / Peinture`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=4200798420153598) | `KW063-U01` | `REVIEW_REQUIRED`: current terms/legal notice and operator identity were not established. Not requested. |
| `KW063-M02` | peinture pro | `coolay.justsell.store` | Unknown; hosted-domain/store identity link unresolved | Library ID `2270290663891071`; stored ad 12 / observation 101; `DZ / Peinture`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=2270290663891071) | `KW063-U02` | `REVIEW_REQUIRED`: advertiser/store identity and current terms remain unresolved. Not requested. |
| `KW063-M03` | Bricoutillages | `bricoutillages.com` | Unknown; site identifies its public store and location but reviewed pages do not name its legal operator | Library ID `1858565942163492`; stored ad 70 / observation 109; `DZ / Peinture`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=1858565942163492) | `KW063-U03` | `REVIEW_REQUIRED`: sales conditions are referenced but their contents and scope were not resolved; legal operator identity remains unknown. Product route itself uses HTTPS and the relevant robots rule does not disallow `/product/`. Not requested. |
| `KW063-M04` | Vendex DZ | `vendex-dz.com` | Unknown | Library ID `1086235407490463`; stored ad 151 / observation 206; `DZ / Peinture`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=1086235407490463) | `KW063-U04` | `REVIEW_REQUIRED`: terms updated 2026-04-07 restrict copying/republication without written permission; fit with this limited extraction is materially ambiguous. Not requested. |
| `KW063-M05` | Ooredoo Algérie | `www.ooredoo.dz` | Wataniya Télécom Algérie SPA, established | Library ID `1896721121696626`; live Sensor query `smartphone`; ad start date `2026-10-22`. [Ad](https://www.facebook.com/ads/library/?id=1896721121696626) | `KW063-U05` | `REVIEW_REQUIRED` by the explicit Ooredoo rule until its legal-notice and robots ambiguity is resolved. Not used as an initial pilot source. |
| `KW063-M06` | Casa Chic Algerie | `casachicalgerie.com` | Unknown | Library IDs `1508900221277642` and `28183075348011287`; live `meuble algerie` query; start dates `2026-09-14` / `2026-09-10`. [Ad 1](https://www.facebook.com/ads/library/?id=1508900221277642), [Ad 2](https://www.facebook.com/ads/library/?id=28183075348011287) | `KW063-U06` | Source `REVIEW_REQUIRED`; route `EXCLUDED` because it is HTTP/port 80. Terms/robots review remains incomplete. No HTTPS equivalent assumed. |
| `KW063-M07` | UniQa Dz | `dzuniqa.shop` | Unknown | Library ID `1821150515694380`; live `vetement algerie` query; ad start date `2026-10-24`. [Ad](https://www.facebook.com/ads/library/?id=1821150515694380) | `KW063-U07` | `REVIEW_REQUIRED`: current terms/legal evidence, robots, and legal operator were not established. Not requested. |
| `KW063-M08` | Diar Dzair | `alsalam.diardzair.com.dz` | Diar Dzair, e-commerce et innovation; RC `16/00-1013565B18` | Library ID `1494712169083330`; live `electromenager` query; ad start date `2026-09-13`. [Ad](https://www.facebook.com/ads/library/?id=1494712169083330) | Original ad URL is a category/search route. Supplemental route `KW064-U08` was found on the merchant's public catalogue. | `EXCLUDED`: the platform terms restrict reproduction and other use of site data without prior written authorization. No product request. |

## Product-route register

| URL ID | Merchant | URL | Route classification | Eligibility / outcome |
|---|---|---|---|---|
| `KW063-U01` | Kingdom SHOP | [Product route](https://kingdomshop.shop/products/%F0%9F%94%A7-compresseur-d-air-silencieux-9l-dingqi-puissance-silence-et-mobilite) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested. |
| `KW063-U02` | peinture pro | [Product route](https://coolay.justsell.store/product/22865) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested. |
| `KW063-U03` | Bricoutillages | [Product route](https://bricoutillages.com/product/tronconneuse-meuleuse-1010w-115mm-ct13022) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested. Current robots file allows the path, which is not legal permission. |
| `KW063-U04` | Vendex DZ | [Product route](https://vendex-dz.com/product/bache-de-protection-moto-impermeable-anti-uv-poussiere-et-vent/) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested because the terms' copying clause has unresolved scope. |
| `KW063-U05` | Ooredoo Algérie | [Product route](https://www.ooredoo.dz/ar/p/telephone-et-autres/store/samsung-a17-5g) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested. |
| `KW063-U06` | Casa Chic Algerie | [HTTP product route](http://casachicalgerie.com/product/meuble-a-chaussures-2-3-portes/) | Product route candidate from Ad URL; not live-page verified | `EXCLUDED` from the product pilot because it is not HTTPS/443. |
| `KW063-U07` | UniQa Dz | [Product route](https://dzuniqa.shop/products/trench2min) | Product route candidate from Ad URL; not live-page verified | `REVIEW_REQUIRED`; not requested. |
| `KW064-U08` | Diar Dzair | [Product route](https://alsalam.diardzair.com.dz/product/6c311902358a21ca00f506434f58014f) | Supplemental public catalogue route; not the Ad Library landing URL; not live-page verified | `EXCLUDED` by the platform terms. No product facts were used from the search result. |

### Non-product Ad Library route

- **Diar Dzair:** `https://alsalam.diardzair.com.dz/search/?page=1&parent=Electromenager` is a category/search route, not a product page. It remains recorded as the original ad destination and was not requested.
- **IStore.Algerie:** Worker 063 surfaced Library ID `752875360993672` without a landing URL. Its domain and product route remain unknown; it was not promoted into a merchant candidate.
- Two other Casa Chic ads used `bit.ly` destinations. The short links were not followed and did not create verified product routes.

## Source review evidence

All current source assessments below were recorded on **2026-11-01** unless a prior Worker 063 date is stated.

- **Bricoutillages:** The [privacy notice](https://bricoutillages.com/confidentialite/) identifies a public online store, product/app services, a Draria address, and personal-data practices; it does not state a legal operator or resolve the referenced sales conditions. The [contact page](https://bricoutillages.com/contact/) repeats the store's location and contact. The [robots file](https://bricoutillages.com/robots.txt) returned HTTP 200 on 2026-11-01; the `/product/` path is not disallowed. Robots is only a crawl instruction. **Result: `REVIEW_REQUIRED`.**
- **Vendex DZ:** The current [terms](https://vendex-dz.com/terms/) state that site content belongs to VENDEX or its licensors and prohibit copying/republication without prior written permission; review date shown by the terms is 2026-04-07. The scope for a limited internal observation is material and unresolved. **Result: `REVIEW_REQUIRED`.**
- **Ooredoo:** [Robots](https://www.ooredoo.dz/robots.txt) has an empty `Disallow`, which is not authorization. The [legal notice](https://www.ooredoo.dz/fr/particuliers/legal-notice) contains use/reproduction limits; the relationship to passive product-field observation remains ambiguous. The legal operator is Wataniya Télécom Algérie SPA, also identified on the [ARPCE GSM page](https://www.arpce.dz/fr/service/gsm). **Result: `REVIEW_REQUIRED` under the special pilot instruction.**
- **Diar Dzair:** The current candidate subdomain's public navigation exposes Conditions Générales D’Utilisation. The reviewed [terms](https://new.diardzair.com.dz/conditionsgenerales) identify Diar Dzair, e-commerce et innovation, its commercial registration, and its Algiers address. They limit site content/data to private customer use and restrict reproduction, transfer, modification, redistribution, retransmission, publication, or commercial exploitation without prior written authorization. The brand/domain family and linked terms identify the same operator; no written authorization was supplied. **Result: `EXCLUDED`.**
- **Kingdom SHOP, peinture pro / JustSell, UniQa Dz:** Current terms/legal and operator identity evidence could not be established from the available source review. Their relevant robots reviews are incomplete. **Results: `REVIEW_REQUIRED`.**
- **Casa Chic Algerie:** Worker 063 had no resolved current terms/robots evidence. The candidate route is separately excluded by the HTTPS/443 limit. **Result: source `REVIEW_REQUIRED`, URL `EXCLUDED`.**
- **E-Sahel:** The Worker 057/061 written-authorization condition remains in force for this source; no documented authorization was supplied. **Result: `EXCLUDED`.**

An empty robots `Disallow`, a missing robots result, and robots permission on one path are not treated as legal authorization or a substitute for the terms review.

## Identity and URL uncertainty

The Ad Library `country=DZ` query indicates the query context, not the legal location of every advertiser. Public-facing brand, storefront, domain, and legal operator are recorded separately. The eight candidates are not asserted to represent eight distinct legal entities. Only the Ooredoo and Diar Dzair operator identities were established in this review; operator distinctness for the others remains unresolved.
