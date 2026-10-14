# Normalization schema design — Worker 012

**Status:** additive schema proposal, extended by Worker 014 with deterministic classification persistence and validated only on temporary databases. It is not a production migration.

### Worker 017: observation-only Facebook evidence and reproducibility

Facebook normalization starts from one `facebook_ad_observations` row. Its `detected_metadata_json` is the only source for text, including nested `raw_source_payload`, observation-local `page_name`, and observation-local `creative_text`. `facebook_ads` is joined only to resolve the entity ID and platform identity needed by `normalization_source_refs`; its `page_name`, `creative_text`, `source_payload_json`, `start_date`, and other mutable columns are never selection or extraction context. If an observation has no usable copy, normalization records an empty selected input and does not fill it from current entity state. Entity-only historical cases remain diagnostic and unavailable to historical normalization until an evidence-bearing observation exists.

The proposed `normalization_runs` stores the exact `selected_input_text`, its `input_field_path`, optional source-payload span offsets, `input_hash`, `selector_version`, and `normalization_version` as distinct values. The FK in `normalization_source_refs` retains the exact source observation ID and entity ID; the source observation itself remains the authority for `observed_at` and the original payload. `normalization_runs.created_at` remains processing time, not source observation time. This preserves source timestamp, source payload, derived selection, and text-normalization version without copying mutable entity state. `selector_version` is part of the normalization-run identity so a selector change remains auditable even if it selects the same text.

Worker 017's read-only measurement used an isolated file copy; the production database was not opened as a SQLite connection. The copy contained 217 Facebook observations: 148 had a non-empty observation-local `raw_source_payload`, 129 selected usable observation-level text before and after the page-name provenance fix, and 88 had no usable copy. Replaying the former entity-page-name context produced the same selected text for all 217 observations; 203 entity rows had a non-NULL page name, but none changed historical text selection. The 47 entity-only historical observations were counted only for diagnostics and were excluded from selection. Entity-field fallbacks used by normalization after the change: 0. The copy had 0 products, 0 aliases, and 0 mappings. The production file SHA-256 was `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF` before and after the measurement.

### Worker 020: marketplace observation-only titles and deterministic rule versions

Marketplace normalization now selects `announcement.raw_title` and other supported textual fields only from the exact `marketplace_listing_observations.source_payload_json`. `marketplace_listings.raw_title` is not a historical fallback; absent snapshot text remains missing. The Ouedkniss sensor already writes each captured title into its observation payload. The selector version is `source_text_selector_v2`; classification and extraction changes are recorded as `classification_v2` and `deterministic_extract_v2`, while Unicode normalization remains `deterministic_v1` and matching remains `deterministic_match_v1`. Worker 020's isolated measurement found 34/51 observation-local marketplace titles and zero entity-title fallbacks.

## A. Current schema findings

The current V2 database contains 17 `marketplace_listings` and 51 `marketplace_listing_observations`, plus 162 `facebook_ads` and 217 `facebook_ad_observations`. `products`, `product_aliases`, and `product_mappings` each contain zero rows. These are the live counts observed for this review; the small sample is not capacity evidence.

Worker 010 found that `normalization/product_normalisalizer.py` is empty. The V2 observation tables have `(entity_id, observed_at)` uniqueness and append-only triggers, so they are suitable raw evidence and should remain untouched. `marketplace_listings.product_id` and `facebook_ads.product_id` are nullable scalar fields; one scalar field cannot represent several mentions in an ad.

The existing `products` table has a canonical name, optional category, and JSON attributes, but no identity key. `product_aliases` records an alias and product but has no source-observation, language, or review provenance. Aliases can be ambiguous across products. The existing `product_mappings` table has `source_type`, a bare integer `source_id`, `product_id`, optional `alias_id`, a 0–1 `confidence`, free-text `mapping_source`, and `created_at`. Its uniqueness is `(source_type, source_id, mapping_source)`. It has no FK to the source tables, source platform, observation or mention key, candidate evidence, status history, reviewer, or method version. It also permits an alias belonging to a different product.

Worker 010's live-data review showed why a source record is not itself a product: Ouedkniss includes real-estate and vehicle-rental listings as well as paint; Facebook ads include campaigns, variants, bundles, accessories, and non-product promotions. The report also identified an earlier `Open Dropdown` creative-text extraction blocker in Facebook data. Worker 011 is recorded as having fixed that extraction issue; this proposal does not change or retest its sensor code.

**Conclusion:** keep source observations as the immutable evidence base. The existing canonical tables can hold basic catalog records and positive entity-level links, but they do not model the required versioned mention → candidate → evidence → decision workflow.

## B. Proposed architecture

```text
Foundation V2 source entity + immutable observation
  → normalization_source_refs (platform/entity/record/observation identity)
  → normalization_runs (input hash + normalized text + normalizer version)
  → normalization_extraction_runs (extractor version)
  → classification_results (label + deterministic rule/version + source evidence)
  → product_mentions (exact source span + supported attributes only)
  → candidate_generation_runs (matcher version + catalog snapshot + input hash)
  → product_candidates (zero, one, or many canonical products)
  → candidate_evidence (queryable reasons; optional AI run)
  → normalization_decision_events (append-only revisions and actor provenance)
  → confirmed_product_mentions (read-only view of the latest MATCHED event)
```

Every path starts from one exact source observation. A Facebook ad may yield zero, one, or many mention rows. Each mention gets its own candidate and decision history. Multiple observations and mentions may point to the same existing `products.id`. No workflow table creates a canonical product.

`normalization_source_refs` is a small observation registry, with one row per processed observation identity. SQLite cannot declare one FK that conditionally targets either source observation table. The registry instead contains a checked, mutually exclusive FK to the appropriate V2 observation table, plus a trigger that verifies the linked observation belongs to the stated source entity and platform. Derived runs then use ordinary foreign keys to this registry row.

## C. Tables and view

All timestamps use the project's ISO-like text convention. Every workflow result/history table below has `created_at`/`occurred_at` provenance and append-only update/delete triggers. Foreign keys use `RESTRICT` where deletion could erase or orphan audit evidence. The schema requires connections to enable `PRAGMA foreign_keys=ON`.

### `normalization_source_refs`

- **Purpose / PK:** bridge a source identity to one exact source observation; `id`.
- **FKs:** exactly one of `marketplace_observation_id` → `marketplace_listing_observations.id` or `facebook_observation_id` → `facebook_ad_observations.id`.
- **Identity/uniqueness:** `(source_platform, source_entity_type, source_record_id, source_observation_id)`. `source_record_id` is the V2 entity PK; the upstream listing/library identifier remains available through the raw entity row.
- **Indexes:** unique identity index plus `ix_normalization_source_entity` for source-record lookup.
- **Write policy/provenance:** append-only. A validation trigger checks platform, entity type, entity PK, and observation FK agree. Same-numbered IDs from different platforms/types cannot collide.

### `normalization_runs`

- **Purpose / PK:** one deterministic normalized-text result; `id`.
- **FKs:** `source_ref_id` → `normalization_source_refs.id`.
- **Idempotency key:** `(source_ref_id, normalization_version, selector_version, input_hash)`.
- **Indexes:** unique key and source/time lookup.
- **Write policy/provenance:** append-only. Stores selector and normalization versions, exact `selected_input_text`, `input_field_path`, optional source-payload span offsets, exact input hash, normalized output, optional language, and processing timestamp. The source observation FK retains the source timestamp and raw payload. It does not copy or update the raw observation.

### `normalization_extraction_runs`

- **Purpose / PK:** versioned extraction result for one normalized input; `id`.
- **FKs:** `normalization_run_id` → `normalization_runs.id`.
- **Idempotency key:** `(normalization_run_id, extraction_version, input_hash)`.
- **Indexes:** normalization-run/time lookup.
- **Write policy/provenance:** append-only, with `complete` or `partial` result status. A complete run may contain zero mentions.

### `classification_labels` and `classification_results`

- **Purpose / PK:** `classification_labels` is a controlled, extensible label registry; `classification_results.id` identifies one immutable classification of one observation input.
- **Supported labels:** the registry starts with `PRODUCT`, `SERVICE`, `REAL_ESTATE`, `EDUCATION`, and `UNKNOWN`. A future label is introduced by adding a valid uppercase code to the registry, rather than changing a closed `CHECK` list.
- **FK lineage:** each result points to `normalization_source_refs`, `normalization_runs`, and `normalization_extraction_runs`. An insert trigger verifies that both run IDs belong to that exact source reference and that the stored input hash matches the normalization run. The source reference, in turn, points to the exact raw observation and verifies its entity/platform identity.
- **Idempotency key:** `(source_ref_id, classification_version, input_hash)`. A matched rule ID is stored as an output; it is not part of the key, so a changed result under the same version/input is detected as version drift instead of silently creating a second row.
- **Indexes:** the unique key supports source-observation lookup; separate indexes support normalization-run, extraction-run, and classification-version lookup.
- **Write policy/provenance:** append-only. Stores the classification label, method, global classification version, specific matched rule ID, source input hash, both processing run IDs, and `classified_at`. `evidence_json` stores the actual matched terms returned by the current classifier; it is `NULL` when there were no matched terms, including `UNKNOWN`. No explanation is synthesized beyond classifier output.

### `product_mentions`

- **Purpose / PK:** one extracted product, bundle, accessory, variant group, or unknown mention; `id`.
- **FKs:** `extraction_run_id` → extraction result; optional `ai_provider_run_id` when AI participated in extraction.
- **Uniqueness:** `(extraction_run_id, mention_index)`.
- **Indexes:** extraction/mention order and classification/time.
- **Write policy/provenance:** append-only. Stores exact `source_text_span`, half-open Unicode-code-point offsets into the selected raw input, normalized mention text, language when known, extraction method/version lineage, confidence label/typed score, and only explicitly supported `brand`, `model`, `category`, `variant`, `quantity`, `capacity`, and structured specifications. Unsupported attributes stay `NULL`.

### `candidate_generation_runs`

- **Purpose / PK:** one candidate search result for one mention; `id`. A run with no child candidates records a valid zero-candidate search.
- **FKs:** `product_mention_id` → `product_mentions.id`.
- **Idempotency key:** `(product_mention_id, matching_version, input_hash)`.
- **Indexes:** mention/time lookup.
- **Write policy/provenance:** append-only. Stores matching version, catalog snapshot hash, complete/partial status, deterministic/AI-suggestion/hybrid generation method, and a hash over all matching inputs.

### `product_candidates`

- **Purpose / PK:** one canonical product proposed for a mention; `id`.
- **FKs:** `candidate_run_id` → candidate search; `product_id` → existing `products.id`.
- **Uniqueness:** `(candidate_run_id, product_id)` and composite `(id, product_id)` for checked decision FKs.
- **Indexes:** product lookup and run/rank.
- **Write policy/provenance:** append-only. Stores optional rank, qualitative confidence, optional numeric score with explicit `score_type`, scoring method version, and optional calibration version. `score` is not constrained to 0–1 or represented as a probability.

### `candidate_evidence`

- **Purpose / PK:** an auditable reason for a candidate; `id`.
- **FKs:** `candidate_id` → candidate; optional `product_alias_id` → existing alias; optional `ai_provider_run_id` → provider run.
- **Uniqueness:** each evidence item is an independent row; no forced one-reason-per-candidate rule.
- **Indexes:** candidate/time and partial AI-run index.
- **Write policy/provenance:** append-only. Queryable `evidence_type`, value, source, optional span, method version, and structured metadata support alias/title/brand/model/identifier/specification/rule/AI evidence. Triggers prevent attaching an alias from another product. An `ai_suggestion` requires an AI-run FK.

### `ai_provider_runs`

- **Purpose / PK:** one provider request/batch outcome; `id`.
- **FKs:** output rows link back through mention/evidence `ai_provider_run_id`.
- **Uniqueness:** partial unique `(provider, provider_request_id)` when the provider supplied an ID. Provider count is not fixed.
- **Indexes:** status/time and provider request ID.
- **Write policy/provenance:** append-only terminal outcomes: `succeeded`, `partial`, or `failed`. Provider, model, request/batch ID, prompt/schema versions, input hash, output version, start/completion time, error fields, and provider metadata remain individually queryable. Provider metadata JSON supplements these audit columns; it is not the only provenance.

### `normalization_decision_events`

- **Purpose / PK:** append-only decision/review history for one mention; `id`.
- **FKs:** `product_mention_id`; optional composite `(candidate_id, product_id)` → a real candidate/product pair. A trigger checks the candidate belongs to this same mention.
- **Uniqueness:** `(product_mention_id, revision_no)`; revision numbers are allocated transactionally by the writer.
- **Indexes:** mention/revision, state/time, candidate/revision, and partial matched-product lookup.
- **Write policy/provenance:** append-only. Stores state, reviewer/actor type and ID, reason, decision method/version, confidence label, typed score, calibration version, and event time. AI cannot write `MATCHED`.

### `confirmed_product_mentions` (view)

- **Purpose / PK:** read-only current relationship view; no stored PK or duplicate rows.
- **FK lineage:** selects the latest decision event per mention and exposes its candidate/product only when that latest state is `MATCHED`.
- **Uniqueness/indexing:** one result per mention by the unique revision key. The underlying matched-product event index supports product lookup.
- **Write policy/provenance:** not writable. Decision history is authoritative, so this view cannot drift from it. `decision_event_id` leads directly to reviewer/method/evidence/source observation.

### Existing `product_mappings`

Keep this table and all existing rows for compatibility; do not delete, rename, or make it the normalization workflow store. It remains useful for existing entity-level callers. The new per-mention confirmed read model is `confirmed_product_mentions`. Do not automatically sync mention matches into `product_mappings`: that table lacks platform and observation identity, cannot link a row to a mention/revision, and uses free-text `mapping_source`. An automatic copy would lose exact provenance and could collide across source platforms. Additive triggers in the proposed schema prevent new alias/product mismatches in `product_mappings` and stop an alias move from invalidating an existing mapping or candidate-evidence row.

## D. Relationship diagram

```text
marketplace_listing_observations ─┐
                                  ├─ normalization_source_refs
facebook_ad_observations ─────────┘            │
                                               └─ normalization_runs
                                                    │
                                              extraction_runs
                                                    │ 1 → 0..N
                                             product_mentions
                                               │          └── optional ai_provider_runs
                                               └─ candidate_generation_runs
                                                    │ 1 → 0..N
                                               product_candidates ── products
                                                    │
                                               candidate_evidence ── product_aliases
                                                    └────────────── ai_provider_runs
                                               product_mentions
                                                    └─ decision_events ── candidates/products
                                                          │
                                            confirmed_product_mentions (view)
```

## E. State and decision model

`UNMAPPED` has no candidate/product. `CANDIDATE`, `REVIEW`, `REJECTED`, and `MATCHED` point to a specific candidate and its product. Each event is a mention-level revision, and a rejection records which candidate was rejected. A later event can select another candidate, then match it. `REJECTED` does not block a different candidate.

Example:

```text
revision 1  CANDIDATE  product A
revision 2  REJECTED   product A  (reviewer + reason)
revision 3  CANDIDATE  product B
revision 4  REVIEW     product B
revision 5  MATCHED    product B
```

The current view returns only the latest revision when it is `MATCHED`. `UNMAPPED` and `REJECTED` remain explicit history; no single mutable status field replaces the event log. An AI event may record a candidate suggestion, but cannot be `MATCHED`. Scores carry their type and method; qualitative labels do not imply calibration.

## F. Idempotency and versioning

- Hash the exact UTF-8 input with SHA-256, including the source field path and a NUL separator: `SHA256(field_path + NUL + raw_text)`. Store as `sha256:<hex>`. This keeps same-text values from distinct fields distinguishable while preserving the required source-observation/version/hash key.
- `normalization_runs` is unique on source observation reference + normalization version + input hash. Use `INSERT ... ON CONFLICT DO NOTHING`, fetch the existing result, and verify the stored output matches. A mismatch is a deterministic-build defect/hash collision; do not overwrite the old result.
- `normalization_extraction_runs` is unique on parent normalization result + extraction version + normalized-input hash. Candidate runs similarly include mention, matcher version, and a hash of mention inputs plus the product-catalog snapshot/rules.
- `classification_results` uses a separately centralized `classification_version` (currently `classification_v2`) and the normalization run's exact source-input hash. The classifier version covers changes to its rule behavior; the specific matched rule is stored on each result. Reprocessing reuses the existing source/version/hash row and verifies its label, method, rule, and evidence. A new classifier version writes a parallel row.
- A new normalization, extraction, or matching version inserts a parallel result. It never mutates an older result. Candidate/decision persistence for one item should run in a SQLite transaction; use `BEGIN IMMEDIATE` when allocating decision revision numbers to serialize writers.

## G. AI provenance

There is no provider adapter or AI code in this deliverable. Future calls insert one immutable `ai_provider_runs` row with provider/model, optional request/batch ID, prompt and schema versions, input hash, output version, status, timing, error details, and provider metadata. Mentions/evidence rows link to that run. A partial or failed call remains auditable without creating a mapping; only structured candidate/evidence rows represent suggestions. An authorized human or deterministic rule must create the `MATCHED` event.

## H. Scale and cardinality

The CEO's **20,000+ Facebook ads/day** is a planning baseline, not measured throughput. For simple daily sizing only, assume 20,000 ads are processed once, each represented by one source observation, with one normalizer, extractor, and matcher version. Additional observations or reprocessing versions add corresponding rows.

| Per-ad mentions | Source refs/day | Normalization + extraction runs/day | Mention rows/day | Candidate-generation runs/day if searched | Candidate/evidence/decision rows |
|---:|---:|---:|---:|---:|---|
| 1 | 20,000 | 20,000 + 20,000 | 20,000 | up to 20,000 | candidates depend on match fanout; evidence depends on reasons; events grow with revisions |
| 2 | 20,000 | 20,000 + 20,000 | 40,000 | up to 40,000 | same variable fanout per mention |
| 5 | 20,000 | 20,000 + 20,000 | 100,000 | up to 100,000 | same variable fanout per mention |

Source refs grow with observations; normalization and extraction runs grow with processed observations and versions; mentions grow with extraction output; candidate runs grow only when search runs; candidate rows grow with candidates retained; evidence rows grow with reasons; decision rows grow with each state/revision. AI provider runs grow with actual calls/batches, not every ad; there is no assumption that every mention is sent to AI. The confirmed view contains at most one current row per matched mention. No candidate, evidence, AI, or review fanout is inferred from the current small sample. The design uses relational evidence rows and stores only source spans/metadata rather than copying full raw payloads into every derived row.

## I. Migration strategy

After CEO approval, prepare a new additive migration (for example `migrations/002_normalization_schema.sql`) that applies after `001_foundation_v2.sql`. Before applying, verify the exact V2 tables/columns, inspect any existing `product_mappings` alias/product inconsistencies, and decide how the future source-capture transaction will register observation refs. Apply only to a disposable copy first, run schema/FK/integrity checks, then plan a separate reviewed production migration with a backup and rollback procedure. This worker created `normalization/schema_v1.sql` as the proposed DDL; it was applied only to temporary test databases. No production migration was performed.

## J. Test strategy and results

`tests/test_normalization_schema.py` creates an isolated SQLite file under the system temporary directory for each test, loads the minimal V2 substrate in `tests/fixtures/normalization_base.sql`, then executes the proposed schema. It uses representative fixture rows only and cleans up the database after each test.

The schema-specific suite has 27 passing checks covering the 18 workflow scenarios plus source-platform references, alias/product consistency, field-path-sensitive input hashes, selector-version identity, exact selected-input/source-timestamp provenance, valid source-span constraints, candidate/mention consistency, foreign-key cleanliness, and extensible classification labels. The full unittest suite passed 132 tests. Engine tests cover all five labels, rule/version/evidence, exact observation/run lineage, idempotency, coexisting classifier versions, cross-platform numeric-ID isolation, raw-data/catalog immutability, transactional rollback, and Facebook temporal-provenance behavior.

Run with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_normalization_schema.py' -v
```

## K. Temporary SQLite implementation

The executable additive schema is `normalization/schema_v1.sql`, including Worker 014's classification registry/results. The test-only V2 substrate is `tests/fixtures/normalization_base.sql`. Every validation database is temporary and isolated; the test suite does not open `data/Database.sqlite`.

## L. Risks and unresolved decisions

- The source registry currently supports the two V2 source entity types. Adding a source type requires a migration adding its observation FK and validation trigger; SQLite cannot provide a general polymorphic FK.
- Writers must extract the named source text field and verify `source_text_span` and offsets against that exact input. SQLite cannot verify a dynamic JSON field path or Unicode substring for every source payload shape.
- Decision revision numbers are unique but sequential allocation is an application transaction responsibility.
- Current source platform strings are not normalized in the foundation (for example the Facebook table has `facebook` and `Facebook, Instagram`). The proposed registry records the exact value; deciding a stable platform vocabulary is a later source-model decision.
- Numeric scoring is deliberately typed but uncalibrated. Calibration data, labels, policy thresholds, reviewer identity format/retention, and source retention/deletion policy remain future decisions.
- The present validation demonstrates DDL behavior and transaction constraints, not 20k/day latency, storage growth, or production throughput. Those require representative load measurement after implementation.

## M. Scope confirmation

No normalization engine or AI was implemented. No production products, aliases, or mappings were inserted. No live capture, production migration, or Ouedkniss/Facebook sensor change was made.
