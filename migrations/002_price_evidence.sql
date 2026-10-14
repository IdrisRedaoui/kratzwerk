-- Price Evidence Layer v1. Apply only after 001_foundation_v2.sql.
-- All evidence and review records are append-only. Source observations remain authoritative.

CREATE TABLE facebook_landing_page_fetches (
    id INTEGER PRIMARY KEY,
    facebook_ad_observation_id INTEGER NOT NULL
        REFERENCES facebook_ad_observations(id) ON DELETE RESTRICT,
    original_url TEXT NOT NULL,
    resolved_url TEXT,
    fetched_at TEXT NOT NULL,
    fetch_status TEXT NOT NULL CHECK (fetch_status IN ('FETCHED', 'INACCESSIBLE', 'FAILED')),
    http_status INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    content_type TEXT,
    content_sha256 TEXT CHECK (content_sha256 IS NULL OR length(content_sha256) = 64),
    fetcher_name TEXT NOT NULL,
    fetcher_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(evidence_json)),
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE INDEX ix_landing_page_fetch_observation_time
    ON facebook_landing_page_fetches(facebook_ad_observation_id, fetched_at);

CREATE TABLE price_evidence (
    id INTEGER PRIMARY KEY,
    source_platform TEXT NOT NULL CHECK (source_platform IN ('Ouedkniss', 'facebook')),
    source_entity_key TEXT NOT NULL,
    marketplace_observation_id INTEGER
        REFERENCES marketplace_listing_observations(id) ON DELETE RESTRICT,
    facebook_landing_page_fetch_id INTEGER
        REFERENCES facebook_landing_page_fetches(id) ON DELETE RESTRICT,
    source_observed_at TEXT NOT NULL,
    price_observed_at TEXT NOT NULL,
    original_url TEXT,
    resolved_url TEXT,
    raw_value_json TEXT CHECK (raw_value_json IS NULL OR json_valid(raw_value_json)),
    raw_price_text TEXT,
    candidate_amount REAL CHECK (candidate_amount IS NULL OR candidate_amount >= 0),
    candidate_amount_max REAL CHECK (
        candidate_amount_max IS NULL OR candidate_amount_max >= 0
    ),
    source_unit TEXT,
    unit_status TEXT NOT NULL CHECK (
        unit_status IN ('CONFIRMED', 'UNRESOLVED', 'UNKNOWN', 'NOT_APPLICABLE')
    ),
    currency_code TEXT,
    currency_basis TEXT NOT NULL CHECK (
        currency_basis IN ('SOURCE_CONFIRMED', 'INDEPENDENTLY_VERIFIED', 'UNKNOWN')
    ),
    price_type TEXT NOT NULL CHECK (
        price_type IN ('REGULAR', 'SALE', 'STARTING_FROM', 'RANGE', 'INSTALLMENT', 'OTHER')
    ),
    product_identity_state TEXT NOT NULL CHECK (
        product_identity_state IN ('VERIFIED', 'UNVERIFIED', 'AMBIGUOUS', 'NOT_APPLICABLE')
    ),
    product_identity_key TEXT,
    extraction_method TEXT NOT NULL,
    extraction_version TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    extraction_status TEXT NOT NULL CHECK (
        extraction_status IN ('SUCCESS', 'MISSING', 'AMBIGUOUS', 'ERROR')
    ),
    quality_status TEXT NOT NULL CHECK (
        quality_status IN (
            'VALID', 'SUSPICIOUS_PLACEHOLDER', 'UNIT_OR_CURRENCY_UNCONFIRMED',
            'MISSING', 'AMBIGUOUS', 'EXTRACTION_ERROR'
        )
    ),
    reason_codes_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
    supporting_evidence_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(supporting_evidence_json)),
    evidence_index INTEGER NOT NULL DEFAULT 0 CHECK (evidence_index >= 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    CHECK (
        (source_platform = 'Ouedkniss'
            AND marketplace_observation_id IS NOT NULL
            AND facebook_landing_page_fetch_id IS NULL)
        OR
        (source_platform = 'facebook'
            AND marketplace_observation_id IS NULL
            AND facebook_landing_page_fetch_id IS NOT NULL)
    ),
    CHECK (
        candidate_amount_max IS NULL
        OR (candidate_amount IS NOT NULL AND candidate_amount_max >= candidate_amount)
    ),
    CHECK (product_identity_state <> 'VERIFIED' OR product_identity_key IS NOT NULL)
);

CREATE INDEX ix_price_evidence_marketplace_observation
    ON price_evidence(marketplace_observation_id) WHERE marketplace_observation_id IS NOT NULL;
CREATE INDEX ix_price_evidence_landing_fetch
    ON price_evidence(facebook_landing_page_fetch_id)
    WHERE facebook_landing_page_fetch_id IS NOT NULL;
CREATE INDEX ix_price_evidence_source_time
    ON price_evidence(source_platform, price_observed_at);

CREATE TABLE price_evidence_reviews (
    id INTEGER PRIMARY KEY,
    price_evidence_id INTEGER NOT NULL REFERENCES price_evidence(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'NEEDS_MORE_EVIDENCE')),
    quality_status TEXT CHECK (
        quality_status IS NULL OR quality_status IN (
            'VALID', 'SUSPICIOUS_PLACEHOLDER', 'UNIT_OR_CURRENCY_UNCONFIRMED',
            'MISSING', 'AMBIGUOUS', 'EXTRACTION_ERROR'
        )
    ),
    currency_code TEXT,
    currency_basis TEXT CHECK (
        currency_basis IS NULL OR currency_basis IN (
            'SOURCE_CONFIRMED', 'INDEPENDENTLY_VERIFIED', 'UNKNOWN'
        )
    ),
    unit_status TEXT CHECK (
        unit_status IS NULL OR unit_status IN (
            'CONFIRMED', 'UNRESOLVED', 'UNKNOWN', 'NOT_APPLICABLE'
        )
    ),
    product_identity_state TEXT CHECK (
        product_identity_state IS NULL OR product_identity_state IN (
            'VERIFIED', 'UNVERIFIED', 'AMBIGUOUS', 'NOT_APPLICABLE'
        )
    ),
    product_identity_key TEXT,
    unit_interpretation TEXT,
    price_type TEXT CHECK (
        price_type IS NULL OR price_type IN (
            'REGULAR', 'SALE', 'STARTING_FROM', 'RANGE', 'INSTALLMENT', 'OTHER'
        )
    ),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
    supporting_evidence_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(supporting_evidence_json)),
    idempotency_key TEXT NOT NULL UNIQUE,
    CHECK (
        product_identity_state IS NULL
        OR product_identity_state <> 'VERIFIED'
        OR product_identity_key IS NOT NULL
    )
);

CREATE INDEX ix_price_evidence_reviews_latest
    ON price_evidence_reviews(price_evidence_id, id DESC);

CREATE TRIGGER trg_price_evidence_platform_guard
BEFORE INSERT ON price_evidence
WHEN NEW.source_platform = 'Ouedkniss'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM marketplace_listing_observations AS o
        JOIN marketplace_listings AS l ON l.id = o.listing_id
        WHERE o.id = NEW.marketplace_observation_id AND l.platform = 'Ouedkniss'
    ) THEN RAISE(ABORT, 'price evidence source must be an Ouedkniss observation') END;
END;

CREATE TRIGGER trg_landing_fetch_no_update BEFORE UPDATE ON facebook_landing_page_fetches
BEGIN SELECT RAISE(ABORT, 'landing page fetches are append-only'); END;
CREATE TRIGGER trg_landing_fetch_no_delete BEFORE DELETE ON facebook_landing_page_fetches
BEGIN SELECT RAISE(ABORT, 'landing page fetches are append-only'); END;
CREATE TRIGGER trg_price_evidence_no_update BEFORE UPDATE ON price_evidence
BEGIN SELECT RAISE(ABORT, 'price evidence is append-only'); END;
CREATE TRIGGER trg_price_evidence_no_delete BEFORE DELETE ON price_evidence
BEGIN SELECT RAISE(ABORT, 'price evidence is append-only'); END;
CREATE TRIGGER trg_price_evidence_reviews_no_update BEFORE UPDATE ON price_evidence_reviews
BEGIN SELECT RAISE(ABORT, 'price evidence reviews are append-only'); END;
CREATE TRIGGER trg_price_evidence_reviews_no_delete BEFORE DELETE ON price_evidence_reviews
BEGIN SELECT RAISE(ABORT, 'price evidence reviews are append-only'); END;

-- Point-price analytics only: ranges, installment values, and starting-from offers
-- need separate treatment and are deliberately excluded from this view.
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
  AND COALESCE(r.currency_code, e.currency_code) IS NOT NULL
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
  AND e.candidate_amount_max IS NULL
  AND COALESCE(r.price_type, e.price_type) IN ('REGULAR', 'SALE');
