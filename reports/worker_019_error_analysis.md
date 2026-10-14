# Worker 019 — Deterministic normalization error analysis

Date: 2026-10-07 (Asia/Riyadh). Read-only diagnosis; no AI design or implementation.

## CEO summary

Worker 018 reproduces: **39 mentions; 21 MATCHED/HIGH, 6 REVIEW/UNCERTAIN, 12 UNMAPPED/NO_MATCH, 0 REJECTED**. A second pass processed all 268 observations and inserted zero derived rows. All six REVIEW outcomes are intentional synthetic-catalog probes: three shared aliases and three category conflicts.

The 12 unmapped rows comprise nine paint-word false-positive classifications and three uncatalogued generic “Peinture LAC A EAU” titles. The former originate at classification; the latter at benchmark alias/catalog coverage. The 27/27 retrieval proxy is benchmark-only, not production recall. Production has zero products, aliases, and mappings.

Facebook observation 128 contains Arabic CROWN CT31012 / 80 watt evidence; normalization preserves it, but classification returns UNKNOWN and blocks mention extraction. This is a deterministic Arabic vocabulary/attribute-pattern gap.

Marketplace normalization falls back to mutable `marketplace_listings.raw_title` for 17/51 snapshots. Those 17 are dated 2026-09-22 and their entity rows were last seen 2026-10-05/06; eight fallback rows are product mentions. This is a historical provenance violation with temporal leakage risk.

## 1. Worker 018 reproduction

- Isolated database: `data/worker_018_benchmark.sqlite`; manifest and synthetic catalog unchanged.
- Outcomes: 21 MATCHED/HIGH; 6 REVIEW/UNCERTAIN; 12 UNMAPPED/NO_MATCH; 0 REJECTED. 27 mentions have candidates; 12 have none; 30 candidate rows.
- Idempotency pass: 268 observations processed; counts unchanged at 268 source refs/normalization/extraction/classification rows, 39 mentions/decisions, 39 candidate runs, 30 candidates, and 33 evidence rows. Zero additional rows in all derived tables.
- Three `Peinture FIXATEUR` observations share an exact alias across two synthetic products. Three `Peinture IMPRONTA` observations match a deliberately wrong-category alias attached to a compressor. REVIEW is the correct result.
- Benchmark exact matches are against synthetic Worker 018 catalog entries, not production products.
- Facebook selected evidence is observation-local only. Usable-text baseline remains 129/217 = 59.45%. No Facebook entity fields were used.

## 2. Stage diagnosis

| Stage | Finding |
|---|---|
| A. Source availability | CROWN and paint examples exist in Facebook observation text. Seventeen marketplace snapshots lack their title. |
| B. Text extraction | Facebook selector selects only the first creative line after Sponsored. CROWN's selected line contains product type, CROWN, CT31012, and 80 watt; later observation-local lines are omitted. |
| C. Normalization | NFKC/casefold and dash normalization preserve Arabic title, model, and spec. No relevant loss in the 39 mentions. |
| D. Classification | Nine false positives arise because generic `peinture` alone triggers PRODUCT. Arabic CROWN vocabulary is absent, so CROWN is UNKNOWN. |
| E. Mention extraction | All 39 PRODUCT rows emit one whole-span mention. CROWN emits none because extraction is gated on PRODUCT. Kits remain `mention_kind=product`; `kit` does not produce bundle kind. |
| F. Candidate generation | Exact alias lookup works. Zero-candidate mentions have no matching alias in the synthetic catalog. |
| G. Alias/catalog coverage | Three “Peinture LAC A EAU” titles lack benchmark aliases; production has no catalog. |
| H. Matching | Unique aliases match HIGH; alias ambiguity/conflict correctly produces REVIEW. |
| I. Provenance | Marketplace fallback reads mutable entity state for historical observations. Facebook observation-only contract is preserved. |
| J. Genuine ambiguity | Shared FIXATEUR and conflicting IMPRONTA probes lack distinguishing input and should remain REVIEW. |

## 3. Complete 39-mention inventory

Classifier rule and matched term are shown beside the persisted extractor method. Candidate aliases are synthetic benchmark aliases, not production aliases.

| Source / observation | Exact mention text | Classification | Extraction rule | Candidate count | Candidate aliases | Final state | Confidence | Failure stage | Likely fix category |
|---|---|---|---|---:|---|---|---|---|---|
| Facebook obs 70<br>observation-local | La peinture blanche qui sublime vos intérieurs. | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18002 French white interior paint campaign: “La peinture blanche qui sublime vos intérieurs.” [facebook] | MATCHED | HIGH | — | — |
| Facebook obs 71<br>observation-local | La peinture blanche qui sublime vos intérieurs. | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18002 French white interior paint campaign: “La peinture blanche qui sublime vos intérieurs.” [facebook] | MATCHED | HIGH | — | — |
| Facebook obs 76<br>observation-local | 🎉 LE COMPRESSEUR QUI CHANGE TOUT ! 🔧 | PRODUCT<br>explicit_product_terms_v1 (compresseur) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18001 Compressor campaign (unidentified model): “🎉 LE COMPRESSEUR QUI CHANGE TOUT ! 🔧” [facebook] | MATCHED | HIGH | — | — |
| Facebook obs 103<br>observation-local | À Changxing, dans la province du Zhejiang, la brume matinale s'élève doucement des vallées, enveloppe les montagnes verdoyantes et teinte le ciel et la terre d'un délicat voile bleu. Les nuages et la brume embrassent les sommets, nous donnant l'impression de pénétrer dans une peinture traditionnelle chinoise. #Zhejiang #ChinaTravel #PaysageChinois | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 107<br>observation-local | À Changxing, dans la province du Zhejiang, la brume matinale s'élève doucement des vallées, enveloppe les montagnes verdoyantes et teinte le ciel et la terre d'un délicat voile bleu. Les nuages et la brume embrassent les sommets, nous donnant l'impression de pénétrer dans une peinture traditionnelle chinoise. #Zhejiang #ChinaTravel #PaysageChinois | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 113<br>observation-local | Explorez des idées de peinture modernes au-delà du beige | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 124<br>observation-local | 20 minutes de peinture reduisent le stress. Des fleurs delicates aux tons pastel, chaque zone numerotee, chaque couleur pre-melangee. | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18003 Paint-by-numbers kit: “20 minutes de peinture reduisent le stress. Des fleurs delicates aux tons pastel, chaque zone numerotee, chaque couleur pre-melangee.” [facebook] | MATCHED | HIGH | — | — |
| Facebook obs 134<br>observation-local | 🏓✨🏞️ Jouer au #pickleball… au milieu d’un paysage digne d’une peinture à l’encre ? | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 137<br>observation-local | Avant / Après impressionnant 😮 ponçage, peinture, vernis… le résultat parle de lui-même 💎 | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 162<br>observation-local | Toile qui gondole, peinture séchée, pinceaux qui perdent leurs poils… Ça vous parle ? Un kit de peinture par numéros bas de gamme peut ruiner tout le plaisir avant même le premier coup de pinceau. Davincified règle ça | PRODUCT<br>explicit_product_terms_v1 (kit, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18003 Paint-by-numbers kit: “Toile qui gondole, peinture séchée, pinceaux qui perdent leurs poils… Ça vous parle ? Un kit de peinture par numéros bas de gamme peut ruiner tout le plaisir avant même le premier coup de pinceau. Davincified règle ça” [facebook] | MATCHED | HIGH | — | — |
| Facebook obs 164<br>observation-local | Explorez des idées de peinture modernes au-delà du beige | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 165<br>observation-local | Explorez des idées de peinture modernes au-delà du beige | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 207<br>observation-local | Explorez des idées de peinture modernes au-delà du beige | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 211<br>observation-local | La peinture offre expression créative, flexibilité et potentiel d'évolution. Apprenez à démarrer votre carrière et à faire de vos compétences votre métier. | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | D — generic lexical classification emitted false PRODUCT; extraction propagated it. | High-precision negative/context rule; otherwise UNKNOWN. |
| Facebook obs 212<br>observation-local | Sûre, simple à utiliser et sèche en seulement 30 minutes : Antartica est la peinture acrylique lavable et respirante sur laquelle vous pouvez compter même lorsque la température baisse, pour renouveler murs et plafonds de manière impeccable. | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18011 Antartica acrylic washable breathable paint: “Sûre, simple à utiliser et sèche en seulement 30 minutes : Antartica est la peinture acrylique lavable et respirante sur laquelle vous pouvez compter même lorsque la température baisse, pour renouveler murs et plafonds de manière impeccable.” [facebook] | MATCHED | HIGH | — | — |
| Marketplace obs 3<br>entity raw_title fallback | Peinture SOFFITTO | PRODUCT<br>explicit_product_terms_v1 (peinture, soffitto) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18004 Peinture SOFFITTO: “ＰＥＩＮＴＵＲＥ　ＳＯＦＦＩＴＴＯ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 4<br>entity raw_title fallback | Peinture FIXA-MUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18005 Peinture FIXA-MUR: “PEINTURE FIXA–MUR” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 5<br>entity raw_title fallback | Peinture ACRYLIQUE Satiné | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18006 Peinture ACRYLIQUE Satiné: “  PEINTURE   ACRYLIQUE SATINÉ  ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 6<br>entity raw_title fallback | Peinture ACRYLIQUE MAT ACRYL | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18007 Peinture ACRYLIQUE MAT ACRYL: “Peinture ACRYLIQUE MAT ACRYL” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 7<br>entity raw_title fallback | Peinture ACRYLIQUE BLACK MAT | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18008 Peinture ACRYLIQUE BLACK MAT: “Peinture ACRYLIQUE BLACK MAT” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 8<br>entity raw_title fallback | Peinture FIXATEUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 2 | 18005 Peinture FIXA-MUR: “Peinture FIXATEUR” [manual]<br>18009 Peinture FIXATEUR: “Peinture FIXATEUR” [marketplace] | REVIEW | UNCERTAIN | H — shared exact alias; intentional ambiguity probe. | Retain REVIEW; seek distinguishing evidence or fix alias ownership. |
| Marketplace obs 9<br>entity raw_title fallback | Peinture IMPRONTA | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18001 Compressor campaign (unidentified model): “None” [None]<br>18001 Compressor campaign (unidentified model): “Peinture IMPRONTA” [manual] | REVIEW | UNCERTAIN | G/H — deliberate wrong-category alias; matcher correctly flags conflict. | Correct benchmark alias/category; preserve conflict review. |
| Marketplace obs 10<br>entity raw_title fallback | Peinture LAC A EAU | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | G — no exact catalog alias; candidate lookup correctly returned zero. | Catalog/alias coverage after canonical identity is verified. |
| Marketplace obs 27<br>observation payload | Peinture FIXATEUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 2 | 18005 Peinture FIXA-MUR: “Peinture FIXATEUR” [manual]<br>18009 Peinture FIXATEUR: “Peinture FIXATEUR” [marketplace] | REVIEW | UNCERTAIN | H — shared exact alias; intentional ambiguity probe. | Retain REVIEW; seek distinguishing evidence or fix alias ownership. |
| Marketplace obs 28<br>observation payload | Peinture LAC A EAU | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | G — no exact catalog alias; candidate lookup correctly returned zero. | Catalog/alias coverage after canonical identity is verified. |
| Marketplace obs 29<br>observation payload | Peinture ACRYLIQUE BLACK MAT | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18008 Peinture ACRYLIQUE BLACK MAT: “Peinture ACRYLIQUE BLACK MAT” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 30<br>observation payload | Peinture ACRYLIQUE MAT ACRYL | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18007 Peinture ACRYLIQUE MAT ACRYL: “Peinture ACRYLIQUE MAT ACRYL” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 31<br>observation payload | Peinture ACRYLIQUE Satiné | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18006 Peinture ACRYLIQUE Satiné: “  PEINTURE   ACRYLIQUE SATINÉ  ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 32<br>observation payload | Peinture SOFFITTO | PRODUCT<br>explicit_product_terms_v1 (peinture, soffitto) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18004 Peinture SOFFITTO: “ＰＥＩＮＴＵＲＥ　ＳＯＦＦＩＴＴＯ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 33<br>observation payload | Peinture FIXA-MUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18005 Peinture FIXA-MUR: “PEINTURE FIXA–MUR” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 34<br>observation payload | Peinture IMPRONTA | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18001 Compressor campaign (unidentified model): “None” [None]<br>18001 Compressor campaign (unidentified model): “Peinture IMPRONTA” [manual] | REVIEW | UNCERTAIN | G/H — deliberate wrong-category alias; matcher correctly flags conflict. | Correct benchmark alias/category; preserve conflict review. |
| Marketplace obs 35<br>observation payload | Peinture ACRYLIQUE Satiné | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18006 Peinture ACRYLIQUE Satiné: “  PEINTURE   ACRYLIQUE SATINÉ  ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 36<br>observation payload | Peinture FIXA-MUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18005 Peinture FIXA-MUR: “PEINTURE FIXA–MUR” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 37<br>observation payload | Peinture FIXATEUR | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 2 | 18005 Peinture FIXA-MUR: “Peinture FIXATEUR” [manual]<br>18009 Peinture FIXATEUR: “Peinture FIXATEUR” [marketplace] | REVIEW | UNCERTAIN | H — shared exact alias; intentional ambiguity probe. | Retain REVIEW; seek distinguishing evidence or fix alias ownership. |
| Marketplace obs 38<br>observation payload | Peinture SOFFITTO | PRODUCT<br>explicit_product_terms_v1 (peinture, soffitto) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18004 Peinture SOFFITTO: “ＰＥＩＮＴＵＲＥ　ＳＯＦＦＩＴＴＯ” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 39<br>observation payload | Peinture LAC A EAU | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 0 | — | UNMAPPED | NO_MATCH | G — no exact catalog alias; candidate lookup correctly returned zero. | Catalog/alias coverage after canonical identity is verified. |
| Marketplace obs 40<br>observation payload | Peinture IMPRONTA | PRODUCT<br>explicit_product_terms_v1 (peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18001 Compressor campaign (unidentified model): “None” [None]<br>18001 Compressor campaign (unidentified model): “Peinture IMPRONTA” [manual] | REVIEW | UNCERTAIN | G/H — deliberate wrong-category alias; matcher correctly flags conflict. | Correct benchmark alias/category; preserve conflict review. |
| Marketplace obs 41<br>observation payload | Peinture ACRYLIQUE BLACK MAT | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18008 Peinture ACRYLIQUE BLACK MAT: “Peinture ACRYLIQUE BLACK MAT” [marketplace] | MATCHED | HIGH | — | — |
| Marketplace obs 42<br>observation payload | Peinture ACRYLIQUE MAT ACRYL | PRODUCT<br>explicit_product_terms_v1 (acrylique, peinture) | deterministic_rules_v1; whole selected span; mention 0 | 1 | 18007 Peinture ACRYLIQUE MAT ACRYL: “Peinture ACRYLIQUE MAT ACRYL” [marketplace] | MATCHED | HIGH | — | — |

## 4. Arabic CROWN analysis

Production observation 128's observation-local `card_text` begins with `مسدس رش الدهان الكهربائي CROWN CT31012 – 80 واط`. The same observation includes additional Arabic product copy and a French line naming the electric paint sprayer CROWN CT31012 and a 0.8 mm nozzle. The selector takes the first creative line, but that line is already sufficient to identify the item.

Normalization preserves the Arabic words, `crown ct31012`, `80`, and the dash-normalized title. The classifier returns UNKNOWN because deterministic product terms contain no Arabic equivalent for electric paint sprayer. The extractor then emits no mention. Attribute patterns also would not reliably extract bare `CT31012` (no separator/model marker), `CROWN` (no brand marker), or Arabic `واط` as a watt unit.

Text-based interpretation: CROWN is brand-like; CT31012 is model/reference; Arabic phrase is generic product type; 80 watt is a specification; no variant is stated. Unrelated `crowns` plural hits in other observations are not the CROWN brand.

**Recommendation: DETERMINISTIC FIX.** Explicit source evidence removes semantic ambiguity. Add Arabic product vocabulary/model/unit coverage in a future deterministic change; an exact model alias is safe once the canonical product is verified. The isolated benchmark alias is not production identity evidence. Earliest failure: classification, followed by unsupported attribute patterns; not normalization or candidate lookup.

## 5. Paint false positives

All nine rows match `explicit_product_terms_v1` on generic `peinture`; all were emitted as PRODUCT with zero aliases. Expected classes below are inferences from observation-local copy only, not external ground truth. UNKNOWN is the safe result where a supported class is not explicit.

| Facebook obs | Exact text | Current rule | Expected class from copy | Deterministic diagnosis |
|---:|---|---|---|---|
| 103 | À Changxing, dans la province du Zhejiang, la brume matinale s'élève doucement des vallées, enveloppe les montagnes verdoyantes et teinte le ciel et la terre d'un délicat voile bleu. Les nuages et la brume embrassent les sommets, nous donnant l'impression de pénétrer dans une peinture traditionnelle chinoise. #Zhejiang #ChinaTravel #PaysageChinois | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — landscape/travel editorial; “peinture traditionnelle chinoise” is art, not a paint SKU. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 107 | À Changxing, dans la province du Zhejiang, la brume matinale s'élève doucement des vallées, enveloppe les montagnes verdoyantes et teinte le ciel et la terre d'un délicat voile bleu. Les nuages et la brume embrassent les sommets, nous donnant l'impression de pénétrer dans une peinture traditionnelle chinoise. #Zhejiang #ChinaTravel #PaysageChinois | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — same landscape/travel copy as observation 103. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 113 | Explorez des idées de peinture modernes au-delà du beige | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — painting/color inspiration, no paint product identity. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 134 | 🏓✨🏞️ Jouer au #pickleball… au milieu d’un paysage digne d’une peinture à l’encre ? | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — pickleball/travel caption; “peinture à l’encre” is figurative art. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 137 | Avant / Après impressionnant 😮 ponçage, peinture, vernis… le résultat parle de lui-même 💎 | PRODUCT / `explicit_product_terms_v1` (`peinture`) | SERVICE — before/after sanding, painting and varnishing work. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 164 | Explorez des idées de peinture modernes au-delà du beige | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — same inspiration copy as observation 113. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 165 | Explorez des idées de peinture modernes au-delà du beige | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — same inspiration copy as observation 113. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 207 | Explorez des idées de peinture modernes au-delà du beige | PRODUCT / `explicit_product_terms_v1` (`peinture`) | UNKNOWN — same inspiration copy as observation 113. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |
| 211 | La peinture offre expression créative, flexibilité et potentiel d'évolution. Apprenez à démarrer votre carrière et à faire de vos compétences votre métier. | PRODUCT / `explicit_product_terms_v1` (`peinture`) | EDUCATION — career/skills learning promotion, no named paint product. | Generic paint token overrides context. Apply high-precision negative/context phrases before generic product terms; otherwise UNKNOWN. |

These are nine observation rows but six unique text strings: landscape/travel copy (103/107), inspiration copy (113/164/165/207), an ink-painting metaphor in a pickleball caption (134), sanding/painting/varnishing before-after work (137), and a career/skills-learning ad (211). Copy-based expectations: UNKNOWN/editorial for 103/107, 113, 134 and 164/165/207; SERVICE for 137; EDUCATION for 211.

The current classifier applies fixed education/real-estate/service terms before product terms, but these examples lack those exact terms. Adding more positive keywords would worsen precision. Safe deterministic architecture needs phrase/context rules and conservative UNKNOWN for vague copy; the observed strings can be fixed deterministically. This sample does not establish an AI requirement.

## 6. Kit / bundle / accessory gap

| Observation | Exact copy | Current extraction | Interpretation |
|---:|---|---|---|
| Facebook 124 | `20 minutes de peinture reduisent le stress. Des fleurs delicates aux tons pastel, chaque zone numerotee, chaque couleur pre-melangee.` | One whole-span `product`; category paint; `variant=pre-melangee`. | Likely one paint-by-numbers kit (numbered zones and pre-mixed colors); no separate products or optional accessory named. `pre-melangee` likely describes kit contents, not a product variant. |
| Facebook 162 | `Toile qui gondole, peinture séchée, pinceaux qui perdent leurs poils… Ça vous parle ? Un kit de peinture par numéros bas de gamme peut ruiner tout le plaisir avant même le premier coup de pinceau. Davincified règle ça` | One whole-span `product`; category paint; no bundle/accessory mention. | Explicit one product sold as a numbered-paint kit; canvas/paint/brushes are discussed as contents, not independently named products. |

The extractor splits only on spaced plus signs when both sides look like references. It labels `bundle` for “bundle/lot/pack” and `accessory` for accessory words, but not `kit`; it has no composition relation output. Neither ad caused an unmapped result in the benchmark. No product-plus-accessory bundle, independent multi-product offer, or optional accessory is demonstrated. Only obs 124 has the likely spurious variant attribute.

**Recommendation: DEFER UNTIL MORE DATA EXISTS.** Current evidence points to A, one canonical product sold as a kit. If more examples establish reusable contents/relationships, a canonical composition model would fit better than only extending `mention_kind`. AI is not needed to identify the explicitly named kit in obs 162.

## 7. Marketplace provenance

The selector uses observation `announcement.raw_title` for 34/51 rows. The 17 missing-title rows are:

| Observation | Snapshot time | Current entity title selected | Entity first-seen | Entity last-seen |
|---:|---|---|---|---|
| 1 | 2026-09-22 19:05:17 | Location de véhicules exclusivement pour entreprises | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 2 | 2026-09-22 19:05:17 | LOCATION DE VOITURE AEROPORT (ALGER ORAN TLEMCEN) | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 3 | 2026-09-22 19:05:17 | Peinture SOFFITTO | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 4 | 2026-09-22 19:05:17 | Peinture FIXA-MUR | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 5 | 2026-09-22 19:05:17 | Peinture ACRYLIQUE Satiné | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 6 | 2026-09-22 19:05:17 | Peinture ACRYLIQUE MAT ACRYL | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 7 | 2026-09-22 19:05:17 | Peinture ACRYLIQUE BLACK MAT | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 8 | 2026-09-22 19:05:17 | Peinture FIXATEUR | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 9 | 2026-09-22 19:05:17 | Peinture IMPRONTA | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 10 | 2026-09-22 19:05:17 | Peinture LAC A EAU | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 11 | 2026-09-22 19:05:17 | Vente Appartement F3 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 12 | 2026-09-22 19:05:17 | Vente Appartement F3 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 13 | 2026-09-22 19:05:17 | Vente Appartement F2 F3 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 14 | 2026-09-22 19:05:17 | Vente Appartement F2 F3 F4 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 15 | 2026-09-22 19:05:17 | Vente Appartement F2 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 16 | 2026-09-22 19:05:17 | Vente Appartement F3 F4 Alger Cheraga | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |
| 17 | 2026-09-22 19:05:17 | Vente Appartement F2 F3 F4 Boumerdès Dellys | 2026-10-05T20:00:56.550402Z | 2026-10-06T14:43:13.201943Z |

Migration 001 copied `legacy_marketplace_listings.raw_title` into the entity row but put only a migration marker in the synthesized observation payload. The observation timestamp (2026-09-22) came from legacy `last_seen`; the title itself has no title-level timestamp. Later sensor captures set/update `marketplace_listings.raw_title` and `last_seen` together, so the current title was last written on 2026-10-05/06; the original legacy-title capture time cannot be recovered from these rows. The 34 observations that do preserve `announcement.raw_title` all currently equal their entity title; the missing 17 cannot be compared. Thus the selector fallback is mutable current entity state, not observation-local evidence, and may leak post-observation title state. Eight fallback product mentions (observations 3–10) are affected.

**Recommended contract: E, split historical/current state.** Historical normalization must be observation-only; if the snapshot has no title, preserve missing evidence. Entity title can serve an explicitly current-state workflow. Future captures should store the title inside each immutable observation. Any recovery should be versioned derived evidence, not presented as historical observation evidence. The fallback is a temporal-leakage risk and provenance violation for historical normalization. No change made.

## 8. AI eligibility

Denominator: 18 unresolved decisions (6 REVIEW + 12 UNMAPPED), with one primary cause per row.

| Primary category | Count | % of 18 | Interpretation |
|---|---:|---:|---|
| Deterministic fix candidate | 9 | 50.0% | Known paint-context false positives; conservative negative/context rules or UNKNOWN. |
| Insufficient source evidence | 0 | 0.0% | No row is proven unresolvable solely from absent text; the marketplace snapshot defect is covered separately. |
| Deterministic ambiguity | 6 | 33.3% | Deliberate shared-alias and conflict probes; preserve REVIEW. |
| Semantic/contextual interpretation requiring AI | 0 | 0.0% | Semantics are involved in the nine errors, but deterministic handling is available for these examples. |
| Catalog/alias coverage | 3 | 16.7% | “Peinture LAC A EAU” has no alias and lacks brand/model; verify canonical identity first. |
| Data-model limitation | 0 | 0.0% | Kit composition is prospective; the two kit rows are matched. |

**Current sample work that genuinely requires AI: 0/18 (0%).** This is not a claim about unseen future copy. The 9 context errors have a conservative deterministic path; the 6 reviews are designed ambiguities; the 3 remaining titles require catalog/source verification.

## 9. CEO decision matrix

| Issue | Evidence and impact | Deterministic solution? | AI required? | Confidence | Recommendation |
|---|---|---|---|---|---|
| Arabic CROWN CT31012 | Observation 128 explicit type/model/spec; no mention emitted. | Yes: Arabic terms and model/unit patterns; alias after product verification. | No. | High | DETERMINISTIC FIX. |
| Paint false positives | Nine rows match `peinture` without product context. | Yes for observed phrases using negative/context precedence and UNKNOWN fallback. | Not shown. | Medium-high on examples; medium on generalization. | Draft conservative deterministic rules after broader adjudication. |
| FIXATEUR ambiguity | Three observations share alias across two synthetic products. | Only with distinguishing evidence or corrected ownership. | No. | High | Keep REVIEW. |
| IMPRONTA conflict | Three observations hit adversarial paint alias on compressor. | Correct alias/category data; preserve conflict gate. | No. | High | Keep conflict guard. |
| LAC A EAU | Three generic titles; no benchmark alias, brand, or model. | Only after canonical product is verified. | No evidence AI can safely identify unique SKU. | Medium | Verify catalog/source identity; do not invent alias. |
| Marketplace provenance | 17 snapshots omit title; current entity data post-dates them; 8 product rows affected. | Yes: observation-only history and title per new snapshot. | No. | High | Split historical/current-state contract. |
| Kit composition | Two ads emit one `product`; no composition relations. | Future model if repeated examples justify it. | No. | Medium | Defer; gather more examples. |

## 10. Recommended next phase

Stay evidence-led: define a proposal for marketplace historical provenance and Arabic deterministic coverage; adjudicate paint examples against broader observation-local text before drafting precise context rules; verify what canonical item (if any) “LAC A EAU” denotes; gather more kit/bundle/accessory examples before modeling composition. Then rerun the benchmark with adjudicated ground truth. The CEO can decide whether any residual unresolved contextual set merits AI. No AI architecture is selected.

## 11. Tests, safety, and exact files

- Full suite: `python -m unittest discover -s tests -v` — **145 passed**.
- Idempotency recheck: passed; zero rows added.
- Production SHA-256 before: `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF`.
- Production SHA-256 after audit: `5CA539EB7A03D84AAD54B79C353961E78EFC594DF54EB6AA13410CF574AA0AFF` (unchanged).
- Production products / aliases / mappings: 0 / 0 / 0 before and after.
- No production writes; no capture; no AI/provider calls; no dependency installs.
- Production schema and normalization/classifier/extractor/matcher unchanged; product/alias/mapping rows unchanged.
- Only intentional file change: `reports/worker_019_error_analysis.md`. No source, database, manifest, or test file changed.
