# Worker 035 — Human-Labeled Normalisation Evaluation Set

Prepared: 2026-10-17T15:57:06+03:00 (Asia/Riyadh)

## Package

This package prepares a blinded, human annotation set and a separate frozen deterministic baseline. No AI call was made and no labels were assigned automatically as ground truth. The production catalog remains empty; baseline identity states describe the current matcher output only.

## Census and sample

The Worker 034 read-only reproduction found **268 observations**, of which **163** had usable observation-local text. The eligible census contained **129 Facebook** and **34 Ouedkniss** observations. The evaluation sample has **59 observations**:

| Source | Sampled | Current baseline has mentions | Current baseline has no mentions |
| --- | ---: | ---: | ---: |
| Facebook | 47 | 3 | 44 |
| Ouedkniss | 12 | 5 | 7 |
| **Total** | **59** | **8** | **51** |

The target follows the 129:34 source proportions with a 47/13 allocation. Adult, abuse-related, or romance-story ads and one-word UNKNOWN fragments were screened out, inherited controls were held out, and exact duplicate text was represented once per source. Only 12 distinct Ouedkniss texts remained; the final sample is **47 Facebook and 12 Ouedkniss observations (59 total)**. No-mention strata include every currently emitted non-product class with usable examples. A deterministic SHA-256 rank with seed `worker035-2026-10-17-v1` selects rows inside each stratum. This challenge-enriched sample supports error review and is not a production prevalence estimate.

The UNKNOWN stratum includes vehicle-model and price-promotion wording with no deterministic product mention, alongside lifestyle and general advertising copy. These give reviewers material to assess potentially ambiguous cases and possible missed offers; the baseline does not treat them as human labels.

The full eligible census and source selectors were reproduced using the Worker 034 runner. Worker 034 selects Facebook text from the observation's captured card payload and Ouedkniss text from that listing observation's source payload. Mutable entity-level text is not used. The sample records the stable source type and observation reference, selected field path, selector version, and exact-source text hash.

## Exclusions and privacy

The **105 observations without usable observation-local text** were excluded from human labeling: 88 Facebook observations had missing or unusable local copy, and 17 Ouedkniss observations lacked a historical title. No current entity text was substituted. The **18 inherited Worker 034 adjudication controls** were excluded from the sample to keep this new annotation independent of prior labels. Content screening excluded `17` candidates (`{"facebook_ad:adult_or_abuse_narrative": 15, "facebook_ad:single_word_fragment": 2}`) to avoid adult, abuse-related, or romance-story copy and one-word fragments that could be personal names. Exact duplicate local-text candidates were represented once per source (`{"facebook_ad": 14, "marketplace_listing": 16}` repeated-text observations bypassed during selection).

Privacy screening redacted **0 evidence records** with **0 evidence spans**: `{}`. Baseline mention span redactions: `{}`. The packaged evidence was scanned after generation for email addresses, URLs, phone patterns, social handles, and credential/token assignments; the match counts were `{"credential_or_token": 0, "email": 0, "url": 0, "phone": 0, "social_handle": 0}`. The source database was not changed. Exact unredacted evidence is represented by its SHA-256 hash and provenance path.

## Database integrity and reproduction

- SHA-256 before: `sha256:5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`
- SHA-256 after: `sha256:5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`
- Unchanged: **true**
- Production writes: **none**
- Database access in Worker 035 preparation: file hashing only; no SQLite connection
- Stable sample set SHA-256: `sha256:10c6803ffc119ad977871e519379c4e7d389c84f42a91e2c4805e4a270be3c43`

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe normalization\worker_034_evaluate.py
.\.venv\Scripts\python.exe normalization\worker_035_prepare.py
```

Worker 034 reproduces the complete read-only census and frozen engine outputs. Worker 035 reads that census and writes only the five package artifacts listed below. The Worker 034 run also verifies its pure output against a temporary isolated persistence replay; schema application and derived writes occur only in that temporary clone.

## Annotation rules

Use the concise rules and examples in `normalization/evaluation/worker_035_reviewer_guide.md`. The annotation sample fields remain null until a human completes review. Use the separate baseline only after annotations are frozen.

## Deliverables

- `reports/worker_035_annotation_package.md`
- `reports/worker_035_results.json`
- `normalization/evaluation/worker_035_annotation_sample.json`
- `normalization/evaluation/worker_035_reviewer_guide.md`
- `normalization/evaluation/worker_035_baseline.json`
