-- Forward-only trust-policy correction. Migration 002 remains immutable so existing
-- installations retain their recorded checksum and can advance to this policy.

CREATE TRIGGER trg_price_evidence_currency_allowlist
BEFORE INSERT ON price_evidence
WHEN NEW.currency_code IS NOT NULL
 AND NEW.currency_code NOT IN ('DZD', 'EUR', 'USD')
BEGIN
    SELECT RAISE(ABORT, 'unsupported price-evidence currency code');
END;

CREATE TRIGGER trg_price_evidence_reviews_currency_allowlist
BEFORE INSERT ON price_evidence_reviews
WHEN NEW.currency_code IS NOT NULL
 AND NEW.currency_code NOT IN ('DZD', 'EUR', 'USD')
BEGIN
    SELECT RAISE(ABORT, 'unsupported price-evidence review currency code');
END;

-- Keep malformed SQLite storage classes and out-of-policy numbers out of the
-- structured evidence table even when a writer bypasses the Python repository.
-- A missing amount remains valid raw evidence; a present amount must be finite,
-- positive, and no greater than the documented 1e12 policy ceiling.
CREATE TRIGGER trg_price_evidence_amount_policy
BEFORE INSERT ON price_evidence
WHEN (NEW.candidate_amount IS NOT NULL AND (
        typeof(NEW.candidate_amount) NOT IN ('integer', 'real')
        OR NEW.candidate_amount <= 0
        OR NEW.candidate_amount > 1000000000000
    ))
 OR (NEW.candidate_amount_max IS NOT NULL AND (
        typeof(NEW.candidate_amount_max) NOT IN ('integer', 'real')
        OR NEW.candidate_amount_max <= 0
        OR NEW.candidate_amount_max > 1000000000000
        OR NEW.candidate_amount IS NULL
        OR NEW.candidate_amount_max < NEW.candidate_amount
    ))
BEGIN
    SELECT RAISE(ABORT, 'price evidence amount violates the finite positive-price policy');
END;

-- Approval is a distinct authorization step. The repository registers this
-- verifier only after validating an HMAC made by the separately provisioned
-- review authority. Connections without that verifier fail closed.
CREATE TRIGGER trg_price_evidence_reviews_authorization
BEFORE INSERT ON price_evidence_reviews
WHEN NEW.decision = 'APPROVED'
BEGIN
    SELECT CASE WHEN COALESCE(kratzwerk_price_review_authorized(
        NEW.price_evidence_id,
        NEW.decision,
        NEW.quality_status,
        NEW.currency_code,
        NEW.currency_basis,
        NEW.unit_status,
        NEW.product_identity_state,
        NEW.product_identity_key,
        NEW.unit_interpretation,
        NEW.price_type,
        NEW.reviewer,
        NEW.reviewed_at,
        NEW.reason_codes_json,
        NEW.supporting_evidence_json
    ), 0) <> 1
    THEN RAISE(ABORT, 'approved price-evidence review lacks valid authorization') END;
END;

-- Migration 001 copied legacy amounts but the legacy source had no currency field.
-- This view exposes their amount and provenance as currency-unknown without changing
-- the append-only historical observations or inventing a replacement currency.
CREATE VIEW legacy_currency_unconfirmed_observations AS
SELECT
    o.id AS observation_id,
    o.listing_id,
    o.observed_at,
    o.price AS raw_legacy_amount,
    NULL AS currency_code,
    'UNKNOWN' AS currency_basis,
    o.source_payload_json AS source_evidence_json
FROM marketplace_listing_observations AS o
WHERE json_valid(o.source_payload_json)
  AND json_extract(o.source_payload_json, '$.migration') = '001_foundation_v2';

DROP VIEW trusted_price_evidence;

-- Same eligibility rules as Migration 002, with an independent currency allowlist
-- and a gate for amounts migrated from a source schema with no currency field.
CREATE VIEW trusted_price_evidence AS
WITH latest_review AS (
    SELECT r.*
    FROM price_evidence_reviews AS r
    WHERE r.id = (
        SELECT MAX(r2.id) FROM price_evidence_reviews AS r2
        WHERE r2.price_evidence_id = r.price_evidence_id
    )
)
SELECT
    e.id AS price_evidence_id,
    e.source_platform,
    e.source_entity_key,
    e.marketplace_observation_id,
    e.facebook_landing_page_fetch_id,
    e.source_observed_at,
    e.price_observed_at,
    CASE WHEN e.source_platform = 'facebook' THEN f.fetched_at
         ELSE e.price_observed_at END AS price_known_at,
    CASE WHEN e.source_platform = 'facebook' THEN ad_observed.observed_at
         ELSE NULL END AS ad_observed_at,
    e.original_url,
    e.resolved_url,
    e.candidate_amount,
    e.candidate_amount_max,
    COALESCE(r.currency_code, e.currency_code) AS currency_code,
    COALESCE(r.currency_basis, e.currency_basis) AS currency_basis,
    e.source_unit,
    COALESCE(r.unit_status, e.unit_status) AS unit_status,
    r.unit_interpretation,
    COALESCE(r.price_type, e.price_type) AS price_type,
    COALESCE(r.product_identity_state, e.product_identity_state) AS product_identity_state,
    COALESCE(r.product_identity_key, e.product_identity_key) AS product_identity_key,
    e.extraction_method,
    e.extraction_version,
    COALESCE(r.quality_status, e.quality_status) AS quality_status,
    e.reason_codes_json,
    r.id AS latest_review_id
FROM price_evidence AS e
LEFT JOIN latest_review AS r ON r.price_evidence_id = e.id
LEFT JOIN facebook_landing_page_fetches AS f ON f.id = e.facebook_landing_page_fetch_id
LEFT JOIN facebook_ad_observations AS ad_observed
    ON ad_observed.id = f.facebook_ad_observation_id
WHERE e.extraction_status = 'SUCCESS'
  AND r.decision = 'APPROVED'
  AND COALESCE(r.quality_status, e.quality_status) = 'VALID'
  AND COALESCE(r.currency_code, e.currency_code) IN ('DZD', 'EUR', 'USD')
  AND COALESCE(r.currency_basis, e.currency_basis) IN (
      'SOURCE_CONFIRMED', 'INDEPENDENTLY_VERIFIED'
  )
  AND COALESCE(r.unit_status, e.unit_status) IN ('CONFIRMED', 'NOT_APPLICABLE')
  AND COALESCE(r.product_identity_state, e.product_identity_state) = 'VERIFIED'
  AND COALESCE(r.product_identity_key, e.product_identity_key) IS NOT NULL
  AND (
      COALESCE(r.unit_status, e.unit_status) = 'NOT_APPLICABLE'
      OR e.unit_status = 'CONFIRMED'
      OR r.unit_interpretation IS NOT NULL
  )
  AND e.candidate_amount IS NOT NULL
  AND typeof(e.candidate_amount) IN ('integer', 'real')
  AND e.candidate_amount > 0
  AND e.candidate_amount <= 1000000000000
  AND e.candidate_amount_max IS NULL
  AND COALESCE(r.price_type, e.price_type) IN ('REGULAR', 'SALE')
  AND (
      NOT EXISTS (
          SELECT 1
          FROM marketplace_listing_observations AS legacy_observation
          WHERE legacy_observation.id = e.marketplace_observation_id
            AND json_valid(legacy_observation.source_payload_json)
            AND json_extract(legacy_observation.source_payload_json, '$.migration') = '001_foundation_v2'
      )
      OR (
          r.currency_code IN ('DZD', 'EUR', 'USD')
          AND r.currency_basis = 'INDEPENDENTLY_VERIFIED'
      )
  );
