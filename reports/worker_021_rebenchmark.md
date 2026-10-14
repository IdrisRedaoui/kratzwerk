# Worker 018 — Deterministic catalog benchmark

Run date: 2026-10-11 (Asia/Riyadh)  
Benchmark database: `C:\Users\Idris\Documents\ChatGPT\Kratzwerk\data\worker_021_benchmark.sqlite`  
Manifest: `C:\Users\Idris\Documents\ChatGPT\Kratzwerk\normalization\worker_018_manifest.json`

## Executive result

The versioned pipeline `deterministic_v1` / `source_text_selector_v2` / `classification_v2` / `deterministic_extract_v2` / `deterministic_match_v1` processed copied production observations against the Worker 018 synthetic catalog. It produced **17 MATCHED**, **4 REVIEW**, **2 UNMAPPED**, and **0 REJECTED** results across **23 product mentions**. The matcher remains unchanged; source selection and deterministic classification/extraction are now versioned.

The benchmark-only catalog covers 20 manifest-grounded mentions. The **benchmark catalog coverage / candidate-retrieval proxy** was 20/20 (100.00%); this is a synthetic benchmark measure, **not production recall**. Current known paint-context false-positive mentions: 0.

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

The unchanged manifest contains 15 independently specified cases: 9 positive, 1 ambiguous, 1 conflict, and 4 negative. Expected values remain exactly as authored for Worker 018; approved Worker 020 behavior changes are recorded separately below.

Unexpected differences: **0**. Approved differences from frozen manifest expectations: **20**.

## 2a. Approved differences from frozen manifest

- italian_soffitto marketplace_listing:3 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18004]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"HIGH"},"final_state":{"actual":"NO_MENTION","manifest":"MATCHED"},"mention_index":{"actual":null,"manifest":0}}`
- dash_normalized_fixa_mur marketplace_listing:4 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18005]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"HIGH"},"final_state":{"actual":"NO_MENTION","manifest":"MATCHED"},"mention_index":{"actual":null,"manifest":0}}`
- unicode_case_whitespace_satine marketplace_listing:5 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18006]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"HIGH"},"final_state":{"actual":"NO_MENTION","manifest":"MATCHED"},"mention_index":{"actual":null,"manifest":0}}`
- similar_acrylique_mat_acryl marketplace_listing:6 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18007]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"HIGH"},"final_state":{"actual":"NO_MENTION","manifest":"MATCHED"},"mention_index":{"actual":null,"manifest":0}}`
- similar_acrylique_black_mat marketplace_listing:7 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18008]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"HIGH"},"final_state":{"actual":"NO_MENTION","manifest":"MATCHED"},"mention_index":{"actual":null,"manifest":0}}`
- ambiguous_fixateur_alias marketplace_listing:8 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18005,18009]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"UNCERTAIN"},"final_state":{"actual":"NO_MENTION","manifest":"REVIEW"},"mention_index":{"actual":null,"manifest":0}}`
- category_conflict_impronta_alias marketplace_listing:9 — historical marketplace title is absent from the observation payload: `{"candidate_product_ids":{"actual":[],"manifest":[18001]},"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"UNCERTAIN"},"decision_method":{"actual":null,"manifest":"conflict_review_v1"},"final_state":{"actual":"NO_MENTION","manifest":"REVIEW"},"mention_index":{"actual":null,"manifest":0}}`
- no_catalog_lac_a_eau marketplace_listing:10 — historical marketplace title is absent from the observation payload: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:103 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:107 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:113 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:134 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:137 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:164 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:165 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:207 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- semantic_paint_false_positive facebook_ad:211 — paint context rules suppress the known non-product mention: `{"classification":{"actual":"UNKNOWN","manifest":"PRODUCT"},"confidence":{"actual":null,"manifest":"NO_MATCH"},"final_state":{"actual":"NO_MENTION","manifest":"UNMAPPED"},"mention_index":{"actual":null,"manifest":0}}`
- non_product_gate_probes marketplace_listing:2 — historical marketplace title is absent from the observation payload: `{"classification":{"actual":"UNKNOWN","manifest":"SERVICE"}}`
- non_product_gate_probes marketplace_listing:11 — historical marketplace title is absent from the observation payload: `{"classification":{"actual":"UNKNOWN","manifest":"REAL_ESTATE"}}`
- arabic_model_product_not_extracted facebook_ad:128 — Arabic CROWN product evidence now emits and matches a mention: `{"candidate_product_ids":{"actual":[18010],"manifest":[]},"classification":{"actual":"PRODUCT","manifest":"UNKNOWN"},"confidence":{"actual":"HIGH","manifest":null},"final_state":{"actual":"MATCHED","manifest":"NO_MENTION"},"mention_index":{"actual":0,"manifest":null}}`

## 3. Input corpus

- Observations: **268** total — Facebook 217, marketplace 51.
- Facebook observation-level usable-text baseline: **129/217 (59.45%)**, matching the declared 129/217 source baseline.
- Product mentions: **23** — Facebook 7, marketplace 16.
- Classification distribution: EDUCATION 3, PRODUCT 23, REAL_ESTATE 14, SERVICE 5, UNKNOWN 223.
- By source: Facebook {'EDUCATION': 3, 'PRODUCT': 7, 'SERVICE': 3, 'UNKNOWN': 204}; marketplace {'PRODUCT': 16, 'REAL_ESTATE': 14, 'SERVICE': 2, 'UNKNOWN': 19}.
- Marketplace observation-local title coverage: 34/51. Entity-title fallback observations: **0**; fallback-derived product mentions: **0**. Facebook non-observation text inputs: **0**.

## 4. Candidate generation

- Mentions with at least one candidate: **21**; zero candidates: **2**.
- Candidate-count distribution per mention: 0: 2, 1: 19, 2: 2.
- Candidate rows: **23**; exact-alias evidence rows: **23**.
- Ambiguous mentions: **2** (4 candidate rows); explicit category-conflict mentions: **2**.
- Evidence by type: {'deterministic_rule': 2, 'exact_alias': 23}.

## Provenance

All 23/23 candidate rows, 25/25 evidence rows, and 23/23 decision events trace to a source observation, product mention, and benchmark canonical product. The 23 exact-alias evidence rows also reference their matching alias; normalization and matching versions are recorded with those runs/evidence. Alias-product link violations: 0; foreign-key violations: 0.

## 5–6. Final outcomes and confidence

- Outcomes: MATCHED 17; REVIEW 4; UNMAPPED 2; REJECTED 0.
- Confidence: HIGH 17; PROBABLE 0; UNCERTAIN 4; NO_MATCH 2.

## 7. Benchmark catalog coverage / candidate-retrieval proxy

The manifest marks 20 product mentions as covered by a positive, ambiguity, or conflict-probe catalog case. A manifest-expected candidate was retrieved for 20 of them: **100.00%**. The denominator includes the three deliberately conflicting aliases, whose expected final result is REVIEW. This proxy measures only this small designed catalog and this copied corpus; it is **not production recall**.

- Deterministic match rate on covered mentions: **80.00%** (17/20).
- Review rate on covered mentions: **20.00%** (4/20).
- Unmapped rate among all product mentions: **8.70%** (2/23).

## 8. False-positive results

False-positive benchmark matches: **0**.  
None.

The exact service, real-estate, and education control texts each have a deliberately inserted manual alias in the isolated catalog and remain gated from product matching. The nine reviewed paint-context observations now produce no PRODUCT mentions. The Arabic CROWN case is an approved manifest delta because the explicit observation evidence now produces a matched mention; its isolated alias remains benchmark-only.

## 9. Idempotency

The identical pipeline was run twice. Additional derived rows on run two: **0** total. Per table: `{'normalization_source_refs': 0, 'normalization_runs': 0, 'normalization_extraction_runs': 0, 'classification_results': 0, 'product_mentions': 0, 'candidate_generation_runs': 0, 'product_candidates': 0, 'candidate_evidence': 0, 'normalization_decision_events': 0}`. All row counts remained stable: **True**.

## 10. Performance

- First full deterministic processing pass: **3.669042 seconds**.
- Throughput: **73.0 observations/second**, **6.3 product mentions/second**.
- Existing exact-alias lookup calls, timed separately over the real product mentions: **0.012197 seconds** for 23 mentions and 23 candidates.

This is a baseline on the current local SQLite/Python runtime; no optimization was attempted.

## 11. Test result

PASS: 154 tests passed; exit code 0.

## 12. Files added or generated

- This run's machine-readable result path, if requested by the caller.
- The frozen manifest was read unchanged; the catalog exists only in the isolated benchmark copy.

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

- Arabic observation 128 now emits CROWN, model CT31012, power-tool category, and the 80 W specification. Its alias is synthetic benchmark data only; production identity remains uncreated.
- Marketplace observations without a captured title remain UNKNOWN; 17/51 historical observations have no observation-local title, and 8 prior entity-fallback product mentions are no longer emitted.
- The nine reviewed paint-context rows now classify UNKNOWN. This is evidence-led handling of those phrases, not a claim that every generic use of `peinture` is non-product.
- The source has two numbered-painting-kit ads, but both are persisted as `mention_kind=product`; bundle wording is not represented as a bundle mention. No accessory-classified source mention was found.
- The Arabic CROWN mention now exposes model/reference `CT31012` and an 80 W specification; the compressor ad gives no model or capacity.
- The current exact-alias matcher distinguishes the corpus's similar `MAT ACRYL` and `BLACK MAT` titles when they each have one alias, and returns REVIEW for the injected shared FIXATEUR alias and category conflict. Punctuation characters not mapped by `deterministic_v1` remain significant; no fuzzy matching was added.

