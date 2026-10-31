# Worker 062 — Merchant Candidate Register

**Recorded:** 2026-10-31 20:49 Asia/Riyadh

**Decision:** `BLOCKED_PENDING_OPERATOR_DECISIONS`

## Evidence boundary

This register was built from the existing Ad Library Sensor output in `data/Database.sqlite`. The database was opened with SQLite read-only mode and `PRAGMA query_only=ON`; no new Sensor capture was needed. The 148 captured observations all record `country=DZ` and `keyword=Peinture`. The capture query supports Algerian advertising-market presence only; it does not establish that an advertiser or store operator is based in Algeria.

Five stored ad destinations have `/product/` or `/products/` path structure. Four link to public-facing store names and are retained below as provisional merchant candidates. A fifth points to a San Marco brand product route; the stored evidence does not establish an Algerian merchant operator for it, so it is not counted as a merchant candidate. The product routes were not requested. Their URL structure is a candidate signal, not confirmation of page contents.

The sensor's stored `page_name` and product destination provide public-brand/domain associations. Legal operator names, addresses, and evidence that these four brands represent four distinct legal operators are absent. The four rows therefore are not counted as four verified operators.

## Candidate merchants

| Candidate ID | Public-facing store/brand | Domain | Legal operator / identity confidence | Evidence of Algeria market presence | Ad Library provenance | Product-page candidate | Eligibility / unresolved questions |
|---|---|---|---|---|---|---|---|
| `KW062-M01` | Kingdom SHOP | `kingdomshop.shop` | Not established; `UNKNOWN` | Ad returned in the stored Sensor capture with `country=DZ`; this does not establish operator location. | `facebook_ads.id=10`; Library ID `4200798420153598`; `page_source_id=NULL`; observation `76`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=4200798420153598) | `KW062-U01` | `REVIEW_REQUIRED` — operator and distinctness unknown; terms, robots, legal-policy basis, and current access not reviewed. Product title is not in the stored creative; URL slug is only a hint. |
| `KW062-M02` | peinture pro | `coolay.justsell.store` | Not established; `UNKNOWN` | Ad returned in the stored Sensor capture with `country=DZ`; no other Algeria evidence in the associated record. | `facebook_ads.id=12`; Library ID `2270290663891071`; `page_source_id=NULL`; observation `101`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=2270290663891071) | `KW062-U02` | `REVIEW_REQUIRED` — the page name and destination host do not identify the operator; terms, robots, legal-policy basis, and current access not reviewed. Product title/category unavailable. |
| `KW062-M03` | Bricoutillages | `bricoutillages.com` | Not established; `UNKNOWN` | Ad returned in the stored Sensor capture with `country=DZ`; no operator-location evidence in the associated record. | `facebook_ads.id=70`; Library ID `1858565942163492`; `page_source_id=NULL`; observation `109`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=1858565942163492) | `KW062-U03` | `REVIEW_REQUIRED` — legal operator and Algerian business status unverified; terms, robots, legal-policy basis, and current access not reviewed. Product wording appears only in the URL slug. |
| `KW062-M04` | Vendex DZ | `vendex-dz.com` | Not established; `UNKNOWN` | The page name and domain include `DZ`, and the ad appears in the stored `DZ`-targeted capture. Neither establishes a legal operator or local registration. | `facebook_ads.id=151`; Library ID `1086235407490463`; `page_source_id=NULL`; observation `206`; observed `2026-10-06T16:30:50.866027Z`. [Ad](https://www.facebook.com/ads/library/?id=1086235407490463) | `KW062-U04` | `REVIEW_REQUIRED` — legal operator and distinctness unverified; terms, robots, legal-policy basis, and current access not reviewed. Product wording appears only in the URL slug. |

## Identity and eligibility summary

- Public-facing brand/domain records retained as provisional merchant candidates: **4**.
- Legal operators established: **0**. Distinctness between the four legal operators is also unverified.
- Product-page route candidates associated with these four brand records: **4**, one each.
- Candidate merchants eligible for final source review: **0**. All four remain `REVIEW_REQUIRED`.
- No individual written merchant permission is assumed to be universally required. Each exact source still needs documented review of applicable terms, restrictions, legal basis, robots policy, and technical access conditions. A separate live-start authorization remains necessary.
- Existing E-Sahel decision preserved: `EXCLUDED` pending documented written authorization, as recorded in Worker 061. E-Sahel was not found among the Ad Library records used here and is not counted in the four-row Ad-derived register.

The source observations store `creative_text` as the `Open Dropdown` placeholder for these five product-route ads. No product title or category is asserted from that field. The URL slugs are recorded as discovery clues only in the URL register.
