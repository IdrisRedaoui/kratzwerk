# Worker 035 — Reviewer Guide

## Review scope

Review one observation at a time using only its displayed `evidence_text`. The text comes from that exact historical observation and may contain explicit `[REDACTED …]` placeholders for personal contact or credential-like content. Do not replace missing or redacted content with current listing or ad fields. Do not use the separate baseline file while assigning labels; it contains deterministic system output, not ground truth.

The annotation JSON intentionally leaves every label field `null`. Keep `null` for work not completed. After review, use an empty list for `exact_product_name_spans` when no product name is present.

## Classification

- `PRODUCT`: a physical or digital good is being offered, requested, or clearly described as the subject of the observation.
- `SERVICE`: the offer is work or an action, such as repair, rental, installation, or consulting. A product mentioned only as the object of a service is not automatically a product offer.
- `REAL_ESTATE`: the subject is land, a home, or other property for sale or rent.
- `EDUCATION`: the subject is a course, school, training, or educational program.
- `UNKNOWN`: usable text exists, but it does not support any of the four classes well enough to choose one. Do not infer an offer from a slogan, vague promotion, or an isolated generic word.

Choose the best supported class when one is clear. Use `UNKNOWN` when no class is defensible. Do not use `UNKNOWN` just because a brand or model is unfamiliar.

## Product-name spans and normalization

Copy each product-identifying span verbatim from `evidence_text`, including the shortest wording that identifies the item. Preserve the evidence's spelling, script, and punctuation in the span. Add separate spans for separate products. Do not include unrelated sales language, contact details, or unsupported attributes. If a product is clear but no exact name can be copied, leave the span list empty and explain why.

Enter `normalized_product_name` only when a concise canonicalized form is defensible from the evidence. Normalize spacing or obvious casing only; do not translate, expand an abbreviation, guess a brand, or supply a model number. Leave it `null` when no single defensible normalized name exists.

Examples (illustrative only):

| Evidence | Classification | Product-name span / rationale |
| --- | --- | --- |
| `Aspirateur ACME VX-2, neuf` | `PRODUCT` | Span `Aspirateur ACME VX-2`; normalize to `Aspirateur ACME VX-2` if a canonicalized copy is useful. |
| `Réparation de téléphones à domicile` | `SERVICE` | Phones are the service object; there is no product offer to span. |
| `Appartement T2 à louer` | `REAL_ESTATE` | Property rental. |
| `Cours du soir en comptabilité` | `EDUCATION` | Educational service. |
| `Le meilleur choix pour votre maison` | `UNKNOWN` | Promotional text without a supported item or service. |

## Evidence sufficiency

- `SUFFICIENT`: the displayed text supports a stable classification and any product span you record.
- `INSUFFICIENT`: the text is too short, fragmentary, or incomplete to determine the class or exact product wording.
- `AMBIGUOUS`: two or more materially different readings remain plausible from the text (for example, a product sale versus a repair request). Record the best class only if one interpretation is clearly stronger; otherwise use `UNKNOWN` and explain the competing readings.

`UNKNOWN` is a classification; `AMBIGUOUS` and `INSUFFICIENT` describe evidence quality. They are not interchangeable. A clear non-offer can be `UNKNOWN` with `SUFFICIENT`; a fragment can be `UNKNOWN` with `INSUFFICIENT`; unresolved competing readings are `AMBIGUOUS`.

## Identity status

- `KNOWN`: the evidence identifies a specific product that you can confidently connect to a known canonical product.
- `NEW_OR_UNLISTED`: the product is clearly identified, but no matching canonical product is present in the review reference set. No catalog is bundled here and the production catalog was empty at preparation time; do not create a catalog entry as part of annotation.
- `UNCERTAIN`: the product class is clear, but its identity cannot be resolved from the evidence, or multiple plausible identities remain.
- `NOT_APPLICABLE`: the classification is not `PRODUCT`, or no product identity is being annotated.

Do not invent a canonical identity. If you cannot distinguish `KNOWN` from `NEW_OR_UNLISTED`, use `UNCERTAIN` and state the reason briefly.

## Rationale and consistency

Write one short reason grounded in the displayed text. Call out missing context, competing readings, or a redaction if it affects the decision. Do not include personal contact details in the rationale. Use the same decision rules across both source types.
