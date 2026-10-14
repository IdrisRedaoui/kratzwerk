# Worker 034 — Normalisation Evaluation Baseline and Offline AI Feasibility

Run date: 2026-10-14 (Asia/Riyadh)
Decision: **NO AI YET**

## Summary

The configured source is `data/Database.sqlite`. Worker 034 read all **268** observation rows: **217 Facebook** and **51 marketplace**. The current deterministic pipeline emitted **23 product mentions** (7 Facebook, 16 marketplace). The production catalog has **0 products, 0 aliases, and 0 mappings**, so all 23 mentions are **UNMAPPED**. This is a catalog-coverage result, not evidence that the extracted products are wrong.

The complete census is unlabeled. A carried-forward set of **18 previously adjudicated real-observation controls** supports narrow classification and mention-presence checks; it is purposive and too small to represent production accuracy. Exact span scoring and live identity accuracy remain unmeasured. No suitable offline model/provider or held-out annotation set is available, so the AI comparison was not run.

## Method and reproducibility

Exact command run:

```powershell
.\.venv\Scripts\python.exe normalization\worker_034_evaluate.py
```

The script opens the configured database through `config.connect(read_only=True)`, enables SQLite `query_only`, starts one read snapshot, and uses the existing source selectors, text normalizer, classifier, mention extractor, and exact-alias matcher. It makes a complete census ordered by source type and observation ID; there is no random sampling. It retains observations without usable observation-local text, labels why their evidence is missing, and does not substitute mutable listing or ad fields.

To exercise the persistence engine, the script also uses SQLite backup to make a temporary clone outside the repository, applies `normalization/schema_v1.sql` to that clone only, and runs `NormalizationEngine.process_all` there. The clone is deleted at the end. Production migrations were not run and the production database was not written.

The versioned manifest records each stable example ID, source type, entity and observation IDs, exact selected observation-local text, selected field path, evidence status, text hash, deterministic output, and matching state. It also records the database hash, code revision, engine versions, sampling rules, and aggregate hashes. The 268-row annotation template has null labels for unreviewed examples; an empty mention list is reserved for a reviewed example with no product mention.

Reproduce the manifest, results, and annotation template with the command above. The pure selector/extractor/matcher census matched the isolated persistence-engine replay. The source database hash was `sha256:5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff` both before and after the run.

## Sample and evidence availability

| Source | Observations | Usable local text | Local field present, no usable copy | Missing local text |
| --- | ---: | ---: | ---: | ---: |
| Facebook ads | 217 | 129 (59.4%) | 19 (8.8%) | 69 (31.8%) |
| Marketplace listings | 51 | 34 (66.7%) | 0 | 17 (33.3%) |
| **Total** | **268** | **163 (60.8%)** | **19 (7.1%)** | **86 (32.1%)** |

Facebook's 69 missing entries have no captured card text. Nineteen have a captured card field but no usable ad copy after source-local selection. The 17 marketplace entries have no title in their own historical observation. All remain in the manifest with empty selected text and a missing-evidence status. Their `UNKNOWN` engine outputs are retained in all-observation totals but excluded from text-eligible classification counts.

## Deterministic baseline

The table shows current engine outputs, not ground truth. Each column counts observations.

| Source | Text eligible | PRODUCT | SERVICE | REAL_ESTATE | EDUCATION | UNKNOWN (all / eligible) | Product mentions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Facebook ads | 129 | 7 | 3 | 0 | 3 | 204 / 116 | 7 |
| Marketplace listings | 34 | 16 | 2 | 14 | 0 | 19 / 2 | 16 |
| **Total** | **163** | **23** | **5** | **14** | **3** | **223 / 118** | **23** |

There are 245 observations with no emitted product mention and 23 with a mention. The exact-alias decision states across the 23 mentions are **MATCHED 0, REVIEW 0, UNMAPPED 23, REJECTED 0**. Thus REVIEW is 0/23 and UNMAPPED is 23/23 of product mentions. This follows directly from the empty production catalog; it is not a successful identity-resolution score.

### Ground-truth metrics and limits

No independent Worker 034 annotations exist for the full production census, so full-census classification precision/recall/F1 and exact mention-span precision/recall/F1 are not estimable. Unlabeled engine outputs are not counted as labels.

The prior Worker 021 residual adjudication in `reports/worker_021_adjudication.md` supplies a small, real-observation control set carried forward in `normalization/evaluation/worker_034_reference_labels.json`:

- 9 product-positive cases: paint-by-numbers kit observations 124 and 162; Arabic CROWN sprayer observation 128; LAC A EAU observations 28 and 39; FIXATEUR observations 27 and 37; IMPRONTA observations 34 and 40.
- 9 non-product paint-context cases: Facebook observations 103, 107, 113, 134, 137, 164, 165, 207, and 211.

On this **purposive inherited control set**, current class outputs agree with all 18 labels: 9/9 PRODUCT and 9/9 UNKNOWN. The one-vs-rest precision, recall, and F1 are 1.00 for PRODUCT and UNKNOWN. SERVICE, REAL_ESTATE, and EDUCATION have no examples in this labeled subset, so those metrics are undefined. At observation-level mention presence, TP=9, FP=0, FN=0, TN=9 (precision/recall/F1=1.00). Those are regression-control results, not production accuracy. The inherited labels do not contain independently reviewed character spans, so exact span metrics remain unavailable.

No current false-positive or false-negative classification can be established across the unlabeled census. Historical issues are still informative but are reported as such:

- The prior Worker 018 run produced nine paint-context product mentions for metaphor, inspiration, repair, or career copy. The later adjudication judged these non-product examples; the current v2 rules classify the nine as UNKNOWN and emit no mention.
- Eight marketplace mentions in the old run depended on mutable entity titles. The current source-local selector emits no text or mention for the affected observations, which lack historical title evidence. These are missing-evidence exclusions, not proven false negatives.
- The paint-by-numbers copy is detected as PRODUCT, but the extractor represents it as a generic product mention rather than a bundle with components. The available source does not establish a complete component list or SKU.

## Exact-alias matching

Production identity matching is blocked: the catalog contains zero products, aliases, and mappings, so no production alias candidates can be retrieved and production identity accuracy cannot be measured. All 23 extracted mentions remain UNMAPPED; none is assigned an invented canonical identity.

For context only, the existing Worker 021 benchmark replay used a deliberately synthetic catalog in an isolated database. It retrieved the expected benchmark candidate for 20/20 manifest-covered mentions and produced 17 MATCHED, 4 REVIEW, and 2 UNMAPPED results across 23 mentions. Those results measure a designed synthetic corpus/catalog proxy only; they are **not production accuracy or production recall**.

## Offline AI feasibility

No AI call was made. The repository contains no local model or provider adapter; `normalization/SCHEMA_DESIGN.md` also states that the current deliverable has no provider adapter or AI code. The 18 inherited controls are not a large, independently labeled dataset and cannot provide a fair held-out comparison. Empty catalog coverage separately prevents product-identity evaluation.

An offline comparison therefore has these blockers:

1. No configured offline model/provider, model version, or execution adapter.
2. No sufficiently sized, independently labeled production subset with exact mention spans and a held-out partition.
3. No populated, adjudicated catalog for identity-matching evaluation.

If those blockers are removed, the bounded next experiment should compare one offline model pass against the frozen deterministic v2 classifier, using only observation-local text and the five existing labels. Require structured output containing a class, short exact evidence quotes, and an abstain flag. Freeze the source-observation split before inference; use a source-stratified held-out set only if the independent labels support it, otherwise mark the run exploratory. Report per-class precision/recall/F1, macro F1, abstentions, failures, and a reviewed error table. Keep inference on a frozen subset with no production database connection, no writes, and no price or identity approval task. Record provider/model/version, prompt/config version, settings, inputs/outputs, failures, cost if available, and reproducibility limits. Do not tune iteratively.

Model, prompt/configuration version, output format, failures, cost, and reproducibility limits are not applicable because no experiment ran. The decision is **NO AI YET**. Reconsider a bounded offline comparison after independent annotations exist and an offline model is made available; identity evaluation also needs an adjudicated catalog.

## Isolation and verification

The temporary full-engine clone processed all 268 observations and persisted 268 normalization runs, 268 classifications, 23 product mentions, and 23 UNMAPPED decision events. SQLite integrity check returned `ok`, and the foreign-key check found 0 violations. The production database hash was unchanged before and after; production schema and catalog were not modified.

Existing normalization tests were inspected before creating the evaluation tooling. The full test suite was not rerun for this evaluation; Worker 021's prior report records 154 passing tests on 2026-10-11. No migrations, dependency changes, production catalog rows, price approvals, or live-pipeline AI changes were made.

## Artifacts

- Complete census and exact source text: `normalization/evaluation/worker_034_manifest.json`
- Human annotation instructions/template for all stable IDs: `normalization/evaluation/worker_034_annotation_template.json`
- Inherited adjudication controls with explicit provenance and limitations: `normalization/evaluation/worker_034_reference_labels.json`
- Machine-readable aggregate results and metrics: `reports/worker_034_results.json`
- Reproduction runner: `normalization/worker_034_evaluate.py`
