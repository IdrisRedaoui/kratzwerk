# Worker 018 — Deterministic catalog benchmark

Run date: 2026-10-07 (Asia/Riyadh)  
Benchmark database: `C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\worker_018_benchmark.sqlite`  
Manifest: `C:\Users\Idris\Documents\ChatGPT\Kratzwerk\normalization\worker_018_manifest.json`

## Executive result

The existing `deterministic_v1` / `deterministic_match_v1` pipeline processed the copied production observations against a small, explicitly synthetic catalog. It produced **21 MATCHED**, **6 REVIEW**, **12 UNMAPPED**, and **0 REJECTED** results across **39 product mentions**. No matcher or source-selection code was changed.

The benchmark-only catalog covers 27 manifest-grounded mentions. The **benchmark catalog coverage / candidate-retrieval proxy** was 27/27 (100.00%); this is a synthetic benchmark measure, **not production recall**. The corpus also exposes false-positive risk in upstream PRODUCT classification: 9 paint-word metaphor/inspiration/repair/career mentions were classified as PRODUCT, then remained unmapped because no exact catalog alias matched.

## 1. Benchmark catalog

- Products: **11**
- Alias rows: **17**
- Production-style mappings in the copy: **0**

Concepts were derived from observed source text only. The catalog includes deliberate ambiguity, conflict, and non-product gate aliases labeled as probes in the manifest; those are test fixtures, not factual production aliases.

- `18001` Compressor campaign (unidentified model) — A real compressor ad is present, but the text contains no model or capacity. The IMPRONTA and rental aliases are intentionally adversarial controls, not factual product aliases.
- `18002` French white interior paint campaign — The observations contain a real French paint advertisement. The manual real-estate alias is only a negative classification-gate probe.
- `18003` Paint-by-numbers kit — Both real ads describe numbered-painting kits. The production extractor currently records them as mention_kind=product, so the benchmark exposes that bundle wording is not represented as a bundle mention.
- `18004` Peinture SOFFITTO — The corpus uses the Italian word SOFFITTO in repeated paint titles. This is a direct Unicode NFKC equivalence probe.
- `18005` Peinture FIXA-MUR — Both FIXA-MUR and FIXATEUR are real paint titles in the corpus. The shared alias is explicitly adversarial and should not collapse the two products into an automatic match.
- `18006` Peinture ACRYLIQUE Satiné — This repeated real French paint title tests case, whitespace, and Unicode canonical normalization.
- `18007` Peinture ACRYLIQUE MAT ACRYL — One of two near-neighbor acrylic paint titles, included to test exact distinction without fuzzy matching.
- `18008` Peinture ACRYLIQUE BLACK MAT — The exact alias differs from MAT ACRYL and should retrieve only its own canonical product.
- `18009` Peinture FIXATEUR — This deliberately shared alias must generate two candidates and require review.
- `18010` مسدس رش الدهان الكهربائي CROWN CT31012 – 80 واط — This is a corpus-derived Arabic product and model/reference case. The alias exists in the isolated catalog, but deterministic_v1 classifies the observation UNKNOWN and creates no mention to match.
- `18011` Antartica acrylic washable breathable paint — An explicit French paint ad with a named product concept; the source text grounds the canonical name and attributes.

## 2. Ground-truth manifest

The manifest contains 15 independently specified cases: 9 positive, 1 ambiguous, 1 conflict, and 4 negative. Each records its source observation, expected candidate set, expected final state and confidence, and rationale. Expected values are authored in the manifest, not inferred from matcher output.

All 15 cases passed their expected candidate, state, confidence, and classification checks.

## 3. Input corpus

- Observations: **268** total — Facebook 217, marketplace 51.
- Facebook observation-level usable-text baseline: **129/217 (59.45%)**, matching the declared 129/217 source baseline.
- Product mentions: **39** — Facebook 15, marketplace 24.
- Classification distribution: EDUCATION 3, PRODUCT 39, REAL_ESTATE 21, SERVICE 6, UNKNOWN 199.
- By source: Facebook {'EDUCATION': 3, 'PRODUCT': 15, 'SERVICE': 3, 'UNKNOWN': 196}; marketplace {'PRODUCT': 24, 'REAL_ESTATE': 21, 'SERVICE': 3, 'UNKNOWN': 3}.
- Marketplace text provenance: {'marketplace_listing_observations.source_payload_json.announcement.raw_title': 34, 'marketplace_listings.raw_title': 17}. The existing selector fell back to `marketplace_listings.raw_title` for 17 of 51 marketplace observations (including 8 emitted product mentions), because those snapshots lack `announcement.raw_title`. This existing marketplace fallback is disclosed separately; Facebook selected text came only from observation-level fields (0 entity-field inputs).

## 4. Candidate generation

- Mentions with at least one candidate: **27**; zero candidates: **12**.
- Candidate-count distribution per mention: 0: 12, 1: 24, 2: 3.
- Candidate rows: **30**; exact-alias evidence rows: **30**.
- Ambiguous mentions: **3** (6 candidate rows); explicit category-conflict mentions: **3**.
- Evidence by type: {'deterministic_rule': 3, 'exact_alias': 30}.

## Provenance

All 30/30 candidate rows, 33/33 evidence rows, and 39/39 decision events trace to a source observation, product mention, and benchmark canonical product. The 30 exact-alias evidence rows also reference their matching alias; normalization and matching versions are recorded with those runs/evidence. Alias-product link violations: 0; foreign-key violations: 0.

## 5–6. Final outcomes and confidence

- Outcomes: MATCHED 21; REVIEW 6; UNMAPPED 12; REJECTED 0.
- Confidence: HIGH 21; PROBABLE 0; UNCERTAIN 6; NO_MATCH 12.

## 7. Benchmark catalog coverage / candidate-retrieval proxy

The manifest marks 27 product mentions as covered by a positive, ambiguity, or conflict-probe catalog case. A manifest-expected candidate was retrieved for 27 of them: **100.00%**. The denominator includes the three deliberately conflicting aliases, whose expected final result is REVIEW. This proxy measures only this small designed catalog and this copied corpus; it is **not production recall**.

- Deterministic match rate on covered mentions: **77.78%** (21/27).
- Review rate on covered mentions: **22.22%** (6/27).
- Unmapped rate among all product mentions: **30.77%** (12/39).

## 8. False-positive results

False-positive benchmark matches: **0**.  
None.

The exact service, real-estate, and education control texts each had a deliberately inserted manual alias in the isolated catalog. The existing classifier gate emitted no product mentions for those records, so none became candidates or matches. The nine paint-word metaphor/inspiration/repair/career examples were classified PRODUCT by existing rules, but all stayed UNMAPPED in this catalog.

## 9. Idempotency

The identical pipeline was run twice. Additional derived rows on run two: **0** total. Per table: `{'normalization_source_refs': 0, 'normalization_runs': 0, 'normalization_extraction_runs': 0, 'classification_results': 0, 'product_mentions': 0, 'candidate_generation_runs': 0, 'product_candidates': 0, 'candidate_evidence': 0, 'normalization_decision_events': 0}`. All row counts remained stable: **True**.

## 10. Performance

- First full deterministic processing pass: **2.738418 seconds**.
- Throughput: **97.9 observations/second**, **14.2 product mentions/second**.
- Existing exact-alias lookup calls, timed separately over the real product mentions: **0.010516 seconds** for 39 mentions and 30 candidates.

This is a baseline on the current local SQLite/Python runtime; no optimization was attempted.

## 11. Test result

Full suite: `python -m unittest discover -s tests -v` — 145 tests passed in 42.061 seconds.

## 12. Files added or generated

- `normalization/worker_018_benchmark.py`
- `normalization/worker_018_manifest.json`
- `tests/test_normalization_benchmark_worker_018.py`
- `reports/worker_018_benchmark.md`
- `reports/worker_018_benchmark_results.json`
- `data/worker_018_benchmark.sqlite` (isolated working copy)

## 13. Production database integrity

- SHA-256 before: `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF`
- SHA-256 after:  `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF`
- Equal: **True**
- Production `products` / `product_aliases` / `product_mappings` before and after: `{'before': {'products': 0, 'product_aliases': 0, 'product_mappings': 0}, 'after': {'products': 0, 'product_aliases': 0, 'product_mappings': 0}}`.
- Normalization schema applied to production: **No**.
- Source observation table fingerprints matched before and after isolated processing: **True**.

## 14. Required scope confirmations

- No production writes: **confirmed**; the source was opened read-only and cloned with SQLite backup.
- No Facebook capture: **confirmed**.
- No AI/provider calls: **confirmed**; the isolated `ai_provider_runs` table has 0 rows.
- No production catalog rows, aliases, or mappings: **confirmed**; counts remained zero.
- No dependencies installed: **confirmed**.
- Production schema unchanged: **confirmed**.

## 15. Remaining matcher and upstream weaknesses

- Arabic observation 128 contains a CROWN CT31012 electric paint sprayer and an 80 W specification. Its exact catalog alias is present in the isolated database, but the current classifier emits UNKNOWN and no product mention; model matching therefore cannot run.
- The nine paint-word metaphor/inspiration/repair/career texts are classified as PRODUCT by the existing lexical gate. The exact matcher does not map them in this catalog, but the classification itself is a false-positive risk.
- The source has two numbered-painting-kit ads, but both are persisted as `mention_kind=product`; bundle wording is not represented as a bundle mention. No accessory-classified source mention was found.
- No product mention in this observation corpus exposes a model/reference or capacity attribute to the existing extractor. The Arabic CROWN model is not emitted due to classification; the compressor ad gives no model or capacity.
- The current exact-alias matcher distinguishes the corpus's similar `MAT ACRYL` and `BLACK MAT` titles when they each have one alias, and returns REVIEW for the injected shared FIXATEUR alias and category conflict. Punctuation characters not mapped by `deterministic_v1` remain significant; no fuzzy matching was added.

