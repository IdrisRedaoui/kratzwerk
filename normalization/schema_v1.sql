-- Proposed additive Normalization schema for Foundation V2.
-- Apply only after migrations/001_foundation_v2.sql has created the V2 tables.
-- This file is design/test material; it has NOT been applied to Database.sqlite.

CREATE TABLE normalization_source_refs (
    id INTEGER PRIMARY KEY,
    source_platform TEXT NOT NULL,
    source_entity_type TEXT NOT NULL
        CHECK (source_entity_type IN ('marketplace_listing', 'facebook_ad')),
    source_record_id INTEGER NOT NULL,
    source_observation_id INTEGER NOT NULL,
    marketplace_observation_id INTEGER
        REFERENCES marketplace_listing_observations(id) ON DELETE RESTRICT,
    facebook_observation_id INTEGER
        REFERENCES facebook_ad_observations(id) ON DELETE RESTRICT,
    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (source_entity_type = 'marketplace_listing'
         AND marketplace_observation_id = source_observation_id
         AND facebook_observation_id IS NULL)
        OR
        (source_entity_type = 'facebook_ad'
         AND facebook_observation_id = source_observation_id
         AND marketplace_observation_id IS NULL)
    ),
    UNIQUE (source_platform, source_entity_type, source_record_id, source_observation_id)
);

CREATE INDEX ix_normalization_source_entity
    ON normalization_source_refs(source_platform, source_entity_type, source_record_id);

CREATE TRIGGER trg_normalization_source_refs_validate_insert
BEFORE INSERT ON normalization_source_refs
BEGIN
    SELECT CASE
      WHEN NEW.source_entity_type = 'marketplace_listing'
       AND NOT EXISTS (
           SELECT 1
           FROM marketplace_listing_observations AS o
           JOIN marketplace_listings AS l ON l.id = o.listing_id
           WHERE o.id = NEW.marketplace_observation_id
             AND l.id = NEW.source_record_id
             AND l.platform = NEW.source_platform
       )
      THEN RAISE(ABORT, 'source reference does not match marketplace observation')
      WHEN NEW.source_entity_type = 'facebook_ad'
       AND NOT EXISTS (
           SELECT 1
           FROM facebook_ad_observations AS o
           JOIN facebook_ads AS a ON a.id = o.ad_id
           WHERE o.id = NEW.facebook_observation_id
             AND a.id = NEW.source_record_id
             AND a.platform = NEW.source_platform
       )
      THEN RAISE(ABORT, 'source reference does not match Facebook observation')
    END;
END;

CREATE TABLE ai_provider_runs (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_request_id TEXT,
    prompt_version TEXT,
    schema_version TEXT,
    input_hash TEXT NOT NULL,
    output_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'partial', 'failed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_code TEXT,
    error_summary TEXT,
    provider_metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(provider_metadata_json)),
    CHECK (completed_at IS NULL OR completed_at >= started_at),
    CHECK ((status = 'failed' AND error_code IS NOT NULL)
        OR status != 'failed')
);

CREATE UNIQUE INDEX ux_ai_provider_request
    ON ai_provider_runs(provider, provider_request_id)
    WHERE provider_request_id IS NOT NULL;
CREATE INDEX ix_ai_provider_status_time
    ON ai_provider_runs(status, started_at);

CREATE TABLE normalization_runs (
    id INTEGER PRIMARY KEY,
    source_ref_id INTEGER NOT NULL
        REFERENCES normalization_source_refs(id) ON DELETE RESTRICT,
    normalization_version TEXT NOT NULL,
    selector_version TEXT NOT NULL,
    input_field_path TEXT NOT NULL,
    input_span_start INTEGER,
    input_span_end INTEGER,
    selected_input_text TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((input_span_start IS NULL) = (input_span_end IS NULL)
        AND (input_span_start IS NULL
             OR (input_span_start >= 0 AND input_span_end >= input_span_start))),
    UNIQUE (source_ref_id, normalization_version, selector_version, input_hash)
);

CREATE INDEX ix_normalization_runs_source_created
    ON normalization_runs(source_ref_id, created_at DESC);

CREATE TABLE normalization_extraction_runs (
    id INTEGER PRIMARY KEY,
    normalization_run_id INTEGER NOT NULL
        REFERENCES normalization_runs(id) ON DELETE RESTRICT,
    extraction_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (normalization_run_id, extraction_version, input_hash)
);

CREATE INDEX ix_extraction_runs_normalization
    ON normalization_extraction_runs(normalization_run_id, created_at DESC);

-- The label registry is intentionally data-driven: future classification labels
-- can be added without changing the classification result table's constraints.
CREATE TABLE classification_labels (
    label TEXT PRIMARY KEY
        CHECK (length(label) > 0 AND label = upper(label)
            AND label NOT GLOB '*[^A-Z0-9_]*')
);

INSERT INTO classification_labels(label) VALUES
    ('PRODUCT'), ('SERVICE'), ('REAL_ESTATE'), ('EDUCATION'), ('UNKNOWN');

CREATE TABLE classification_results (
    id INTEGER PRIMARY KEY,
    source_ref_id INTEGER NOT NULL
        REFERENCES normalization_source_refs(id) ON DELETE RESTRICT,
    normalization_run_id INTEGER NOT NULL
        REFERENCES normalization_runs(id) ON DELETE RESTRICT,
    extraction_run_id INTEGER NOT NULL
        REFERENCES normalization_extraction_runs(id) ON DELETE RESTRICT,
    classification_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    classification_label TEXT NOT NULL
        REFERENCES classification_labels(label) ON DELETE RESTRICT,
    classification_method TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    evidence_json TEXT CHECK (evidence_json IS NULL OR json_valid(evidence_json)),
    classified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_ref_id, classification_version, input_hash)
);

-- The unique key supports observation lookups. These indexes support run and
-- classifier-version lookups without indexing unrelated result fields.
CREATE INDEX ix_classification_results_normalization_run
    ON classification_results(normalization_run_id);
CREATE INDEX ix_classification_results_extraction_run
    ON classification_results(extraction_run_id);
CREATE INDEX ix_classification_results_version
    ON classification_results(classification_version);

CREATE TRIGGER trg_classification_results_validate_insert
BEFORE INSERT ON classification_results
WHEN NOT EXISTS (
    SELECT 1
    FROM normalization_runs AS n
    JOIN normalization_extraction_runs AS x
      ON x.normalization_run_id = n.id
    WHERE n.id = NEW.normalization_run_id
      AND x.id = NEW.extraction_run_id
      AND n.source_ref_id = NEW.source_ref_id
      AND n.input_hash = NEW.input_hash
)
BEGIN
    SELECT RAISE(ABORT, 'classification provenance does not match run inputs');
END;

CREATE TABLE product_mentions (
    id INTEGER PRIMARY KEY,
    extraction_run_id INTEGER NOT NULL
        REFERENCES normalization_extraction_runs(id) ON DELETE RESTRICT,
    mention_index INTEGER NOT NULL CHECK (mention_index >= 0),
    source_span_start INTEGER NOT NULL CHECK (source_span_start >= 0),
    source_span_end INTEGER NOT NULL CHECK (source_span_end > source_span_start),
    source_text_span TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language_code TEXT,
    mention_kind TEXT NOT NULL
        CHECK (mention_kind IN ('product', 'bundle', 'accessory', 'variant_group', 'unknown')),
    brand TEXT,
    model TEXT,
    category TEXT,
    variant TEXT,
    quantity_value REAL,
    quantity_unit TEXT,
    capacity_value REAL,
    capacity_unit TEXT,
    specifications_json TEXT
        CHECK (specifications_json IS NULL OR json_valid(specifications_json)),
    extraction_method TEXT NOT NULL,
    extraction_confidence TEXT
        CHECK (extraction_confidence IS NULL
            OR extraction_confidence IN ('HIGH', 'PROBABLE', 'UNCERTAIN')),
    extraction_score REAL,
    extraction_score_type TEXT,
    ai_provider_run_id INTEGER REFERENCES ai_provider_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (extraction_run_id, mention_index),
    CHECK ((extraction_score IS NULL AND extraction_score_type IS NULL)
        OR (extraction_score IS NOT NULL AND extraction_score_type IS NOT NULL))
);

CREATE INDEX ix_product_mentions_extraction
    ON product_mentions(extraction_run_id, mention_index);
CREATE INDEX ix_product_mentions_kind
    ON product_mentions(mention_kind, created_at);

CREATE TABLE candidate_generation_runs (
    id INTEGER PRIMARY KEY,
    product_mention_id INTEGER NOT NULL
        REFERENCES product_mentions(id) ON DELETE RESTRICT,
    matching_version TEXT NOT NULL,
    catalog_snapshot_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    generation_method TEXT NOT NULL
        CHECK (generation_method IN ('deterministic', 'ai_suggestion', 'hybrid')),
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_mention_id, matching_version, input_hash)
);

CREATE INDEX ix_candidate_generation_mention
    ON candidate_generation_runs(product_mention_id, created_at DESC);

CREATE TABLE product_candidates (
    id INTEGER PRIMARY KEY,
    candidate_run_id INTEGER NOT NULL
        REFERENCES candidate_generation_runs(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    rank INTEGER CHECK (rank IS NULL OR rank > 0),
    confidence_label TEXT
        CHECK (confidence_label IS NULL
            OR confidence_label IN ('HIGH', 'PROBABLE', 'UNCERTAIN')),
    score REAL,
    score_type TEXT,
    scoring_method_version TEXT NOT NULL,
    calibration_version TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (candidate_run_id, product_id),
    UNIQUE (id, product_id),
    CHECK ((score IS NULL AND score_type IS NULL)
        OR (score IS NOT NULL AND score_type IS NOT NULL))
);

CREATE INDEX ix_product_candidates_product
    ON product_candidates(product_id);
CREATE INDEX ix_product_candidates_run_rank
    ON product_candidates(candidate_run_id, rank);

CREATE TABLE candidate_evidence (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL
        REFERENCES product_candidates(id) ON DELETE RESTRICT,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN (
        'exact_alias', 'normalized_title', 'brand_match', 'model_match',
        'identifier_match', 'specification_match', 'deterministic_rule', 'ai_suggestion'
    )),
    evidence_value TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    source_span_start INTEGER,
    source_span_end INTEGER,
    method_version TEXT,
    product_alias_id INTEGER REFERENCES product_aliases(id) ON DELETE RESTRICT,
    ai_provider_run_id INTEGER REFERENCES ai_provider_runs(id) ON DELETE RESTRICT,
    evidence_metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(evidence_metadata_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((source_span_start IS NULL AND source_span_end IS NULL)
        OR (source_span_start >= 0 AND source_span_end > source_span_start)),
    CHECK ((evidence_type = 'ai_suggestion' AND ai_provider_run_id IS NOT NULL)
        OR (evidence_type != 'ai_suggestion' AND ai_provider_run_id IS NULL))
);

CREATE INDEX ix_candidate_evidence_candidate
    ON candidate_evidence(candidate_id, created_at);
CREATE INDEX ix_candidate_evidence_ai_run
    ON candidate_evidence(ai_provider_run_id)
    WHERE ai_provider_run_id IS NOT NULL;

CREATE TRIGGER trg_candidate_evidence_alias_product_insert
BEFORE INSERT ON candidate_evidence
WHEN NEW.product_alias_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1
     FROM product_candidates AS c
     JOIN product_aliases AS a ON a.id = NEW.product_alias_id
     WHERE c.id = NEW.candidate_id AND c.product_id = a.product_id
 )
BEGIN
    SELECT RAISE(ABORT, 'candidate alias belongs to a different product');
END;

CREATE TABLE normalization_decision_events (
    id INTEGER PRIMARY KEY,
    product_mention_id INTEGER NOT NULL
        REFERENCES product_mentions(id) ON DELETE RESTRICT,
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    decision_state TEXT NOT NULL CHECK (decision_state IN (
        'UNMAPPED', 'CANDIDATE', 'REVIEW', 'MATCHED', 'REJECTED'
    )),
    candidate_id INTEGER,
    product_id INTEGER,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'rule', 'system', 'ai')),
    actor_id TEXT,
    decision_method TEXT NOT NULL,
    decision_method_version TEXT,
    reason TEXT,
    confidence_label TEXT
        CHECK (confidence_label IS NULL
            OR confidence_label IN ('HIGH', 'PROBABLE', 'UNCERTAIN', 'NO_MATCH')),
    score REAL,
    score_type TEXT,
    calibration_version TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (candidate_id, product_id)
        REFERENCES product_candidates(id, product_id) ON DELETE RESTRICT,
    UNIQUE (product_mention_id, revision_no),
    UNIQUE (id, product_mention_id, candidate_id, product_id),
    CHECK (
        (decision_state = 'UNMAPPED' AND candidate_id IS NULL AND product_id IS NULL)
        OR
        (decision_state IN ('CANDIDATE', 'REVIEW', 'MATCHED', 'REJECTED')
         AND candidate_id IS NOT NULL AND product_id IS NOT NULL)
    ),
    CHECK (decision_state != 'MATCHED' OR actor_type != 'ai'),
    CHECK ((score IS NULL AND score_type IS NULL)
        OR (score IS NOT NULL AND score_type IS NOT NULL))
);

CREATE INDEX ix_decision_events_mention_revision
    ON normalization_decision_events(product_mention_id, revision_no DESC);
CREATE INDEX ix_decision_events_state_time
    ON normalization_decision_events(decision_state, occurred_at);
CREATE INDEX ix_decision_events_candidate
    ON normalization_decision_events(candidate_id, revision_no);

CREATE TRIGGER trg_decision_event_candidate_belongs_to_mention
BEFORE INSERT ON normalization_decision_events
WHEN NEW.candidate_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1
     FROM product_candidates AS c
     JOIN candidate_generation_runs AS g ON g.id = c.candidate_run_id
     WHERE c.id = NEW.candidate_id
       AND c.product_id = NEW.product_id
       AND g.product_mention_id = NEW.product_mention_id
 )
BEGIN
    SELECT RAISE(ABORT, 'decision candidate belongs to a different mention');
END;

-- A read-only current-state projection; each event remains the source of truth.
CREATE VIEW confirmed_product_mentions AS
SELECT d.product_mention_id, d.candidate_id, d.product_id,
       d.id AS decision_event_id, d.occurred_at AS confirmed_at
FROM normalization_decision_events AS d
WHERE d.decision_state = 'MATCHED'
  AND d.revision_no = (
      SELECT MAX(newest.revision_no)
      FROM normalization_decision_events AS newest
      WHERE newest.product_mention_id = d.product_mention_id
  );

CREATE INDEX ix_decision_events_matched_product
    ON normalization_decision_events(product_id, occurred_at)
    WHERE decision_state = 'MATCHED';

-- Preserve product_mappings for existing entity-level callers, while preventing
-- the alias/product inconsistency identified in the Worker 010 audit.
CREATE TRIGGER trg_product_mappings_alias_product_insert
BEFORE INSERT ON product_mappings
WHEN NEW.alias_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM product_aliases AS a
     WHERE a.id = NEW.alias_id AND a.product_id = NEW.product_id
 )
BEGIN
    SELECT RAISE(ABORT, 'mapping alias belongs to a different product');
END;

CREATE TRIGGER trg_product_mappings_alias_product_update
BEFORE UPDATE OF product_id, alias_id ON product_mappings
WHEN NEW.alias_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM product_aliases AS a
     WHERE a.id = NEW.alias_id AND a.product_id = NEW.product_id
 )
BEGIN
    SELECT RAISE(ABORT, 'mapping alias belongs to a different product');
END;

CREATE TRIGGER trg_product_aliases_preserve_referenced_product
BEFORE UPDATE OF product_id ON product_aliases
WHEN EXISTS (
    SELECT 1 FROM product_mappings AS m
    WHERE m.alias_id = OLD.id AND m.product_id != NEW.product_id
)
OR EXISTS (
    SELECT 1
    FROM candidate_evidence AS e
    JOIN product_candidates AS c ON c.id = e.candidate_id
    WHERE e.product_alias_id = OLD.id AND c.product_id != NEW.product_id
)
BEGIN
    SELECT RAISE(ABORT, 'cannot move an alias away from a referenced product');
END;

-- Derived workflow rows are immutable evidence. confirmed_product_mentions is a
-- read-only view over append-only events, so current state cannot drift from history.
CREATE TRIGGER trg_normalization_source_refs_no_update
BEFORE UPDATE ON normalization_source_refs
BEGIN SELECT RAISE(ABORT, 'normalization source refs are append-only'); END;
CREATE TRIGGER trg_normalization_source_refs_no_delete
BEFORE DELETE ON normalization_source_refs
BEGIN SELECT RAISE(ABORT, 'normalization source refs are append-only'); END;
CREATE TRIGGER trg_ai_provider_runs_no_update
BEFORE UPDATE ON ai_provider_runs
BEGIN SELECT RAISE(ABORT, 'AI provider runs are append-only'); END;
CREATE TRIGGER trg_ai_provider_runs_no_delete
BEFORE DELETE ON ai_provider_runs
BEGIN SELECT RAISE(ABORT, 'AI provider runs are append-only'); END;
CREATE TRIGGER trg_normalization_runs_no_update
BEFORE UPDATE ON normalization_runs
BEGIN SELECT RAISE(ABORT, 'normalization runs are append-only'); END;
CREATE TRIGGER trg_normalization_runs_no_delete
BEFORE DELETE ON normalization_runs
BEGIN SELECT RAISE(ABORT, 'normalization runs are append-only'); END;
CREATE TRIGGER trg_extraction_runs_no_update
BEFORE UPDATE ON normalization_extraction_runs
BEGIN SELECT RAISE(ABORT, 'extraction runs are append-only'); END;
CREATE TRIGGER trg_extraction_runs_no_delete
BEFORE DELETE ON normalization_extraction_runs
BEGIN SELECT RAISE(ABORT, 'extraction runs are append-only'); END;
CREATE TRIGGER trg_classification_labels_no_update
BEFORE UPDATE ON classification_labels
BEGIN SELECT RAISE(ABORT, 'classification labels are append-only'); END;
CREATE TRIGGER trg_classification_labels_no_delete
BEFORE DELETE ON classification_labels
BEGIN SELECT RAISE(ABORT, 'classification labels are append-only'); END;
CREATE TRIGGER trg_classification_results_no_update
BEFORE UPDATE ON classification_results
BEGIN SELECT RAISE(ABORT, 'classification results are append-only'); END;
CREATE TRIGGER trg_classification_results_no_delete
BEFORE DELETE ON classification_results
BEGIN SELECT RAISE(ABORT, 'classification results are append-only'); END;
CREATE TRIGGER trg_product_mentions_no_update
BEFORE UPDATE ON product_mentions
BEGIN SELECT RAISE(ABORT, 'product mentions are append-only'); END;
CREATE TRIGGER trg_product_mentions_no_delete
BEFORE DELETE ON product_mentions
BEGIN SELECT RAISE(ABORT, 'product mentions are append-only'); END;
CREATE TRIGGER trg_candidate_generation_runs_no_update
BEFORE UPDATE ON candidate_generation_runs
BEGIN SELECT RAISE(ABORT, 'candidate generation runs are append-only'); END;
CREATE TRIGGER trg_candidate_generation_runs_no_delete
BEFORE DELETE ON candidate_generation_runs
BEGIN SELECT RAISE(ABORT, 'candidate generation runs are append-only'); END;
CREATE TRIGGER trg_product_candidates_no_update
BEFORE UPDATE ON product_candidates
BEGIN SELECT RAISE(ABORT, 'product candidates are append-only'); END;
CREATE TRIGGER trg_product_candidates_no_delete
BEFORE DELETE ON product_candidates
BEGIN SELECT RAISE(ABORT, 'product candidates are append-only'); END;
CREATE TRIGGER trg_candidate_evidence_no_update
BEFORE UPDATE ON candidate_evidence
BEGIN SELECT RAISE(ABORT, 'candidate evidence is append-only'); END;
CREATE TRIGGER trg_candidate_evidence_no_delete
BEFORE DELETE ON candidate_evidence
BEGIN SELECT RAISE(ABORT, 'candidate evidence is append-only'); END;
CREATE TRIGGER trg_decision_events_no_update
BEFORE UPDATE ON normalization_decision_events
BEGIN SELECT RAISE(ABORT, 'normalization decision events are append-only'); END;
CREATE TRIGGER trg_decision_events_no_delete
BEFORE DELETE ON normalization_decision_events
BEGIN SELECT RAISE(ABORT, 'normalization decision events are append-only'); END;
