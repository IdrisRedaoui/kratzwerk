PRAGMA foreign_keys = ON;

CREATE TABLE retention_policies (
    policy_id TEXT PRIMARY KEY,
    retention_days INTEGER NOT NULL CHECK (retention_days > 0),
    approved_by TEXT NOT NULL,
    approval_reference TEXT NOT NULL,
    approved_at_utc TEXT NOT NULL
);

CREATE TABLE merchants (
    merchant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    legal_operator_identity TEXT,
    operator_identity_confidence TEXT NOT NULL
        CHECK (operator_identity_confidence IN ('UNKNOWN','LOW','MEDIUM','HIGH')),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE candidate_urls (
    candidate_url_id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE RESTRICT,
    candidate_url TEXT NOT NULL,
    url_classification TEXT NOT NULL,
    eligibility_status TEXT NOT NULL
        CHECK (eligibility_status IN ('ELIGIBLE_FOR_FINAL_REVIEW','REVIEW_REQUIRED','EXCLUDED','INSUFFICIENT_EVIDENCE')),
    eligibility_reason TEXT NOT NULL,
    source_ad_record_id INTEGER,
    source_ad_observation_id INTEGER,
    source_ad_library_id TEXT,
    source_ad_url TEXT,
    discovery_provenance_json TEXT NOT NULL
        CHECK (json_valid(discovery_provenance_json) AND json_type(discovery_provenance_json)='object'),
    created_at_utc TEXT NOT NULL,
    UNIQUE (merchant_id, candidate_url)
);

CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    merchant_id TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE RESTRICT,
    candidate_url_id TEXT NOT NULL REFERENCES candidate_urls(candidate_url_id) ON DELETE RESTRICT,
    source_ad_record_id INTEGER,
    source_ad_observation_id INTEGER,
    source_ad_library_id TEXT,
    source_ad_url TEXT,
    discovery_provenance_json TEXT NOT NULL
        CHECK (json_valid(discovery_provenance_json) AND json_type(discovery_provenance_json)='object'),
    observed_at_utc TEXT NOT NULL,
    retention_policy_id TEXT NOT NULL REFERENCES retention_policies(policy_id) ON DELETE RESTRICT,
    retention_due_at_utc TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    redirect_history_json TEXT NOT NULL
        CHECK (json_valid(redirect_history_json) AND json_type(redirect_history_json)='array'),
    final_url TEXT,
    http_status INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    fetch_outcome TEXT NOT NULL,
    fetch_error_code TEXT,
    extraction_outcome TEXT NOT NULL,
    raw_observed_values_json TEXT NOT NULL
        CHECK (json_valid(raw_observed_values_json) AND json_type(raw_observed_values_json)='object'),
    normalized_interpretations_json TEXT NOT NULL
        CHECK (json_valid(normalized_interpretations_json) AND json_type(normalized_interpretations_json)='object'),
    content_sha256 TEXT CHECK (content_sha256 IS NULL OR
        (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*')),
    fetcher_version TEXT,
    extraction_version TEXT
);

CREATE INDEX ix_research_observations_merchant_time
    ON observations(merchant_id, observed_at_utc, observation_id);
CREATE INDEX ix_research_observations_candidate_time
    ON observations(candidate_url_id, observed_at_utc, observation_id);

CREATE TRIGGER trg_research_observations_no_update
BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'research observations are append-only'); END;
CREATE TRIGGER trg_research_observations_no_delete
BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'research observations are append-only'); END;
