# Worker 021 — Rebenchmark and Residual Case Adjudication

Run date: 2026-10-11 (Asia/Riyadh)

## Scope and method

This was a read-only validation. The configured database, data/Database.sqlite, was opened read-only and copied with SQLite backup into fresh isolated databases before normalization. The current implementation was measured directly and the Worker 018 benchmark was rerun from a separate fresh copy. The benchmark added its 11 synthetic products and 17 aliases only to that copy. No provider, capture, marketplace request, or Facebook request was made.

The starting database had 217 Facebook ad observations and 51 Ouedkniss listing observations (268 total), 162 Facebook ad entities and 17 marketplace listings. Production products, aliases, and mappings were each 0.

Current identifiers are source_text_selector_v2, deterministic_extract_v2, and classification_v2. Unicode normalization remains deterministic_v1, and matching remains deterministic_match_v1. The Worker 018 manifest was read but not changed; its SHA-256 was 7CA5ADBB43E1EAC270D58D03B4CA8292B713D61B125595B039F114179B17E686.

## Measurements

The before values below are the preserved Worker 018 benchmark result. The pre-Worker-020 implementation is not present in the current source tree, so this run did not reconstruct or execute old code. The after values were independently rerun on 2026-10-11.

| Run | Product mentions | MATCHED / HIGH | REVIEW / UNCERTAIN | UNMAPPED / NO_MATCH | REJECTED |
| --- | ---: | ---: | ---: | ---: | ---: |
| Preserved pre-Worker-020 / Worker 018 benchmark | 39 | 21 | 6 | 12 | 0 |
| Worker 021 direct remeasurement, empty production catalog | 23 | 0 | 0 | 23 | 0 |
| Worker 021 Worker 018 benchmark, isolated synthetic catalog | 23 | 17 | 4 | 2 | 0 |

The first after row is the actual current pipeline over production source observations with the real, empty catalog. The benchmark row uses its deliberately seeded isolated test catalog; it does not describe production matches. The benchmark reproduced the expected post-Worker-020 totals exactly: 23 mentions, 17 MATCHED, 4 REVIEW, and 2 UNMAPPED.

The preserved before-to-after benchmark delta is 39 to 23 mentions (-16), 21 to 17 matches (-4), 6 to 4 reviews (-2), and 12 to 2 unmapped (-10). The mention-count change decomposes into nine paint-context mentions suppressed, eight mentions formerly derived from mutable listing-title fallbacks suppressed, and one Arabic CROWN mention recovered.

Marketplace title coverage is 34/51. The other 17 observations have no title in their own snapshot even though all 17 currently have a mutable entity title. The v2 selector uses only the observation payload, produced zero entity-title fallback rows and zero product mentions from missing-title observations, and retained exact observation provenance. The 129 usable Facebook texts were selected from their own observation payloads; 69 observations have no captured card text and 19 have no usable copy.

The Arabic CROWN card at Facebook observation 128 now yields the observed Arabic electric-sprayer phrase, CROWN, model CT31012, and 80 W. The isolated benchmark matches its synthetic alias at HIGH confidence. No production alias or mapping was created.

The nine paint-context controls no longer produce product mentions. The rerun reported zero false-positive benchmark matches. No REJECTED outcomes appeared; the two unresolved products remain UNMAPPED and the four ambiguous/conflicting benchmark cases remain REVIEW.

### Idempotency and provenance

The direct measurement copy and benchmark copy each processed all 268 observations twice. The second run added zero rows to every derived table. Source observation fingerprints matched before and after processing, the isolated database integrity check returned ok, foreign-key checks were clean, and the benchmark recorded zero AI provider runs.

In the direct measurement copy, the real production catalog remained empty, so every emitted product mention had no candidate and was UNMAPPED. In the separate benchmark copy, 20 manifest-covered mentions had 20/20 expected-candidate retrieval; this is a designed corpus/catalog proxy, not production recall. The synthetic catalog is intentionally narrow and cannot measure coverage of a real product catalog.

## Residual case adjudication

| Case | Evidence available and missing | Deterministic/catalog option | AI plausibility | Decision |
| --- | --- | --- | --- | --- |
| 17 marketplace observations without local titles | Their observation payloads have no captured title; the mutable listing rows now have titles. There are no observation-local title bytes to evaluate for those 17 historical observations. | Keep the selected input missing. A future capture can preserve a new title in a new observation; that cannot repair these old snapshots. | No. AI cannot recover unobserved historical text. | No mention is emitted. Treat as missing evidence, not a classification error, UNMAPPED product, or AI task. Do not backfill. |
| LAC A EAU | Two observation-local titles, observations 28 and 39, say Peinture LAC A EAU. Both classify as PRODUCT and extract the normalized phrase. No brand, model, size, SKU, package, or matching production catalog entry is present. | Deterministic extraction already identifies a paint mention. An exact alias could map it only after the catalog policy accepts a generic water-based paint type as a canonical product; an alias alone cannot establish a unique brand or SKU. | Not needed to identify the stated category. AI could paraphrase the French phrase, but that does not add missing product identity. | UNMAPPED until a catalog owner chooses a supported canonical level and supplies coverage. The benchmark deliberately has no matching alias. |
| FIXATEUR / FIXA-MUR | Local titles separately show Peinture FIXATEUR and Peinture FIXA-MUR. The benchmark's shared normalized alias for FIXATEUR retrieves two candidates (18005 and 18009); repeated FIXATEUR evidence remains the same conflict. No packaging, SKU, or independent product reference resolves it. | Exact string extraction is deterministic, but the shared alias must not be made unique by guess. A verified, distinguishing alias or product evidence could resolve a future mapping. | AI cannot choose between two same-alias catalog candidates from this evidence. | REVIEW / UNCERTAIN in the isolated benchmark. With the actual empty production catalog these mentions are UNMAPPED. Preserve the conflict; create no canonical identity. |
| IMPRONTA | Local title Peinture IMPRONTA is available. The benchmark deliberately points that alias to a compressor candidate; the explicit paint category conflicts with that candidate. There is no independent brand/SKU/package evidence for a canonical identity. | Retain the mismatch as conflict evidence. A reviewed catalog correction backed by source evidence could change it; do not attach the alias to the compressor. | AI cannot safely repair a deliberately conflicting alias or establish the paint's identity from this title alone. | REVIEW / UNCERTAIN for the benchmark conflict. With the empty production catalog it is UNMAPPED. Do not manufacture a mapping. |
| Paint-by-numbers kits | Facebook observations 124 and 162 contain kit-oriented ad copy. They classify as PRODUCT, but extraction records a generic product/paint mention, not a bundle or component list. The copy does not establish a complete component inventory or exact SKU. | A catalog may represent a specifically advertised kit as one product and use a reviewed alias. The synthetic benchmark does this and matches both examples; production has no such entry. Component extraction requires a bundle representation that is outside this audit. | AI might parse a richer component list, but these texts do not give enough detail to identify all contents. No demonstrated AI-only task remains. | MATCHED only against the isolated synthetic kit entry. Actual production is UNMAPPED. The limitation is product representation and catalog coverage, with incomplete composition evidence; do not infer components. |
| Paint-context controls | Nine Facebook snapshots describe art/inspiration, metaphor, repair work, or careers rather than an offer for paint. Those exact source texts are available. | The narrow contextual deterministic rules suppress the product classification; no alias or new model is needed. | Not for this reviewed set; current rules removed the observed false-positive mentions. | No product mention. These are classification negatives, not REJECTED product identities. |

The benchmark's 4 REVIEW results are the two FIXATEUR ambiguity observations and two IMPRONTA category-conflict observations. Its 2 UNMAPPED results are the two evidence-bearing LAC A EAU mentions. These statuses were produced only in the isolated benchmark with synthetic aliases.

## AI recommendation: NO-GO

Do not introduce AI normalization or matching for this corpus now. The nine reviewed context cases were handled by narrow deterministic rules; the CROWN product was recovered from explicit Arabic, brand, model, and watt evidence; missing historical titles cannot be inferred; LAC A EAU lacks catalog identity coverage; and FIXATEUR/IMPRONTA are unresolved catalog conflicts. The kit texts do not expose a sufficiently complete composition to evaluate a generated component list.

The current production catalog has no products, aliases, or mappings, so there is no meaningful live identity baseline against which AI could be scored. A future AI evaluation should wait for an adjudicated catalog and labeled examples, preserve REVIEW/UNMAPPED as valid outputs, and define measurable product-identity and false-positive criteria. The current benchmark's perfect candidate-retrieval proxy applies only to its 20 designed cases and synthetic catalog.

## Test result and limitations

Full existing suite: 154 tests passed (unittest discovery; exit code 0).

Confidence is high in the repeated deterministic counts, current schema/code inspection, observation provenance checks, and SQLite integrity checks. Confidence is limited in generalization beyond this 268-observation sample and the synthetic Worker 018 manifest. The pre-Worker-020 comparison comes from the preserved Worker 018 result, not a rerun of removed old code. No production write, live collection, landing-page request, or provider call was performed.

## Reproducibility artifacts

- Isolated direct measurement database: data/worker_021_measurement.sqlite
- Isolated Worker 018 benchmark database: data/worker_021_benchmark.sqlite
- Direct measurement JSON: reports/worker_021_measurement_results.json
- Fresh benchmark JSON: reports/worker_021_rebenchmark_results.json
- Combined Worker 021 measurements and adjudication JSON: reports/worker_021_results.json
