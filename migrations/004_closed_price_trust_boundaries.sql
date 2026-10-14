-- Migration 003's connection-local UDF and caller-supplied fetch claims do not
-- authenticate a source or reviewer. Keep the existing evidence, but make all
-- old and new attestations explicitly unverified until controlled services exist.

ALTER TABLE facebook_landing_page_fetches
    ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (provenance_status IN ('UNVERIFIED', 'VERIFIED'));

ALTER TABLE price_evidence_reviews
    ADD COLUMN authorization_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (authorization_status IN ('UNVERIFIED', 'VERIFIED'));

DROP TRIGGER trg_price_evidence_reviews_authorization;

-- No approval signer/identity provider is implemented in this repository.
-- Refuse APPROVED writes and any attempt to self-assert verified authorization.
CREATE TRIGGER trg_price_evidence_reviews_authorization
BEFORE INSERT ON price_evidence_reviews
WHEN NEW.decision = 'APPROVED' OR NEW.authorization_status <> 'UNVERIFIED'
BEGIN
    SELECT RAISE(ABORT, 'trusted price review service is not configured');
END;

-- Repository fetch submissions are audit input, never fetch-service attestations.
CREATE TRIGGER trg_landing_fetch_attestation_unverified
BEFORE INSERT ON facebook_landing_page_fetches
WHEN NEW.provenance_status <> 'UNVERIFIED'
BEGIN
    SELECT RAISE(ABORT, 'trusted landing-page fetch service is not configured');
END;

DROP VIEW trusted_price_evidence;

-- Preserve the prior eligibility policy and additionally require independently
-- verified fetch and review states. Current repository writes can create neither,
-- so the view intentionally remains empty until real controlled services exist.
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
  AND r.authorization_status = 'VERIFIED'
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
      e.source_platform <> 'facebook'
      OR (
          f.provenance_status = 'VERIFIED'
          AND f.fetch_status = 'FETCHED'
          AND f.http_status BETWEEN 200 AND 299
          AND lower(f.content_type) LIKE 'text/html%'
          AND f.resolved_url IS NOT NULL
      )
  )
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
