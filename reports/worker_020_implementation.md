# Worker 020 — deterministic fixes and isolated remeasurement

Run date: 2026-10-07 (Asia/Riyadh)

## Scope delivered

- Marketplace historical title selection now uses only the exact observation payload. Missing observation titles stay missing; no current entity title is consulted.
- Arabic electric paint-sprayer vocabulary, the observed CROWN model shape, and Arabic watt units are recognized narrowly.
- Five high-precision French paint-context rules keep the reviewed artistic, inspiration, repair-work, and career copy out of PRODUCT.
- Matching architecture, fuzzy matching, embeddings, provider calls, production products, aliases, and mappings were not added.
- `normalization/worker_018_manifest.json` was not modified. The benchmark records expected behavior changes against that frozen baseline.

## Exact rule changes

### Marketplace provenance

`select_ouedkniss_text` now selects `announcement.raw_title` and supported text attributes only from `marketplace_listing_observations.source_payload_json`. `load_source_observation` no longer queries `marketplace_listings.raw_title`. A missing observation title uses an explicit `#missing` evidence path and empty text. The existing Ouedkniss sensor already captures each new title in the observation payload.

### Arabic CROWN evidence

- Product type vocabulary recognizes the observed `مسدس رش الدهان الكهربائي` and conservative spelling variants for an electric paint sprayer; it maps to the existing `power_tool` category.
- `CT` followed by five digits is retained as a model only when the Arabic sprayer phrase and adjacent `CROWN CT…` evidence occur together. `CROWN` is retained as brand evidence under that same condition.
- Specification extraction accepts Arabic `واط` and `وات` watt units.
- The real observed text `مسدس رش الدهان الكهربائي CROWN CT31012 – 80 واط` now emits one PRODUCT mention with brand `CROWN`, model `CT31012`, category `power_tool`, and the `80 واط` specification. No canonical product or production alias was created.

### Paint context

These specific contexts now return UNKNOWN before generic paint vocabulary can produce PRODUCT:

- `peinture traditionnelle chinoise`
- `idée(s) de peinture`
- `peinture à l'encre`
- `ponçage, peinture, vernis`
- career copy containing paint, `apprenez`, `carrière`, `compétences`, and `expression créative`

The rules are phrase/context patterns with explicit `non_product_*_v2` rule IDs. They are not a broad keyword exclusion list. Generic `Peinture ACRYLIQUE Satiné`, the named Antartica paint ad, and paint-by-numbers kit copy remain PRODUCT.

## Versions

| Pipeline stage | Version after change |
|---|---|
| Text normalization | `deterministic_v1` (unchanged) |
| Source selector | `source_text_selector_v2` |
| Classification | `classification_v2` |
| Extraction run | `deterministic_extract_v2` |
| Mention extraction method | `deterministic_rules_v2` |
| Matching | `deterministic_match_v1` (unchanged) |

The v2 selector/classifier/extractor results are parallel derived records; prior v1 evidence is not rewritten.

## Isolated production-copy measurement

The normalization schema and engine were run twice on [worker_020_measurement.sqlite](../data/worker_020_measurement.sqlite), a SQLite-backup copy with no synthetic catalog. The second pass inserted zero derived rows.

| Source / metric | Worker 019 baseline | Worker 020 after |
|---|---:|---:|
| Facebook observations | 217 | 217 |
| Facebook usable text | 129 | 129 |
| Facebook PRODUCT / SERVICE / REAL_ESTATE / EDUCATION / UNKNOWN | 15 / 3 / 0 / 3 / 196 | 7 / 3 / 0 / 3 / 204 |
| Facebook product mentions | 15 | 7 |
| Marketplace observations | 51 | 51 |
| Marketplace observation-local title coverage | 34/51 | 34/51 |
| Marketplace entity-title fallback observations | 17 | **0** |
| Marketplace PRODUCT / SERVICE / REAL_ESTATE / EDUCATION / UNKNOWN | 24 / 3 / 21 / 0 / 3 | 16 / 2 / 14 / 0 / 19 |
| Marketplace product mentions | 24 | 16 |
| Total product mentions | 39 | 23 |

The selector hardening affected 17 historical marketplace observations without a captured title. Eight previously emitted product mentions used those mutable entity titles; they are now absent from historical normalization (five had benchmark MATCHED decisions, two REVIEW, and one UNMAPPED). Their contents cannot be verified from those snapshots. The eight removals are provenance corrections, not paint-classifier losses.

The paint-context rules removed all **9** known false-positive product mentions. The CROWN observation added **1** recovered product mention. **0** valid paint-product cases were lost to the paint rules, and **0** new false positives were observed in the reviewed cases.

## Worker 018 benchmark rerun

The benchmark ran in [worker_020_benchmark.sqlite](../data/worker_020_benchmark.sqlite) with Worker 018's original manifest and its explicitly synthetic benchmark-only catalog. The manifest remains unchanged. The runner accepted only the 20 expected deltas corresponding to the approved fixes: 9 paint contexts, 10 marketplace cases with missing historical titles, and the one recovered Arabic CROWN case. Any other manifest difference still fails the run.

| Outcome | Worker 019 baseline | Worker 020 after |
|---|---:|---:|
| Product mentions | 39 | 23 |
| MATCHED | 21 | 17 |
| REVIEW | 6 | 4 |
| UNMAPPED | 12 | 2 |
| Other false-positive benchmark matches | 0 | 0 |

The outcome changes are expected: removing unsupported historical titles removes five old matches, two reviews, and one unmapped mention; suppressing nine paint false positives removes nine unmapped mentions; CROWN contributes one new MATCHED mention against its synthetic benchmark alias. FIXATEUR and IMPRONTA remain REVIEW where observation evidence still exists. The two evidence-bearing `LAC A EAU` cases remain UNMAPPED; no identity or alias was invented.

## Tests and safety

- Full suite: `python -m unittest discover -s tests -v` — **154 tests passed** in 54.178 seconds.
- Facebook observation-only provenance and existing extraction regressions passed.
- Benchmark second normalization pass added **zero** derived rows; the separate empty-catalog measurement also added zero derived rows on its second pass.
- Production SHA-256 before and after: `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF` (unchanged).
- Production products / aliases / mappings before and after: **0 / 0 / 0**.
- Production schema and observation fingerprints were unchanged. Normalization schema was applied only to isolated copies.
- No production writes, Facebook capture, AI/provider calls, or dependency installation occurred.

## Remaining issues

- Seventeen marketplace history rows lack observation-local titles; they cannot be reconstructed as historical evidence from mutable entity fields.
- Two surviving `LAC A EAU` mentions remain unmapped pending verified source/catalog identity.
- FIXATEUR ambiguity, IMPRONTA category conflict, and kit/bundle composition remain deferred as directed.
- No current unresolved case demonstrated a need for AI.

## Artifacts

- `normalization/extract.py`
- `normalization/persistence.py`
- `normalization/versioning.py`
- `normalization/worker_018_benchmark.py`
- `normalization/SCHEMA_DESIGN.md`
- `tests/test_normalization_engine.py`
- `tests/test_normalization_benchmark_worker_018.py`
- `reports/worker_020_implementation.md`
- [Isolated normalization measurements](worker_020_measurement_results.json)
- [Worker 018 benchmark results](worker_020_benchmark_results.json)
- [Worker 018 benchmark report](worker_020_benchmark.md)
- `data/worker_020_measurement.sqlite` (isolated copy with no synthetic catalog)
- `data/worker_020_benchmark.sqlite` (isolated copy with Worker 018's synthetic benchmark catalog)
