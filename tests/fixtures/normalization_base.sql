-- Minimal isolated Foundation V2 substrate for normalization/schema_v1.sql tests.
-- This is not a production schema and contains no copied production data.
PRAGMA foreign_keys = ON;

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    category TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(attributes_json))
);

CREATE TABLE marketplace_listings (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    raw_title TEXT NOT NULL
);

CREATE TABLE marketplace_listing_observations (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    price REAL,
    currency TEXT NOT NULL DEFAULT 'DZD',
    status TEXT NOT NULL,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (listing_id, observed_at)
);

CREATE TABLE facebook_ads (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'facebook',
    page_name TEXT,
    creative_text TEXT,
    start_date TEXT,
    source_payload_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(source_payload_json))
);

CREATE TABLE facebook_ad_observations (
    id INTEGER PRIMARY KEY,
    ad_id INTEGER NOT NULL REFERENCES facebook_ads(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    detected_metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (ad_id, observed_at)
);

CREATE TABLE product_aliases (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    raw_alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    UNIQUE (product_id, source_type, normalized_alias)
);

CREATE TABLE product_mappings (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    alias_id INTEGER REFERENCES product_aliases(id) ON DELETE SET NULL,
    confidence REAL,
    mapping_source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_type, source_id, mapping_source)
);

CREATE INDEX ix_product_alias_lookup ON product_aliases(source_type, normalized_alias);

CREATE TRIGGER trg_marketplace_observations_no_update
BEFORE UPDATE ON marketplace_listing_observations
BEGIN SELECT RAISE(ABORT, 'marketplace observations are append-only'); END;
CREATE TRIGGER trg_marketplace_observations_no_delete
BEFORE DELETE ON marketplace_listing_observations
BEGIN SELECT RAISE(ABORT, 'marketplace observations are append-only'); END;
CREATE TRIGGER trg_facebook_observations_no_update
BEFORE UPDATE ON facebook_ad_observations
BEGIN SELECT RAISE(ABORT, 'Facebook observations are append-only'); END;
CREATE TRIGGER trg_facebook_observations_no_delete
BEFORE DELETE ON facebook_ad_observations
BEGIN SELECT RAISE(ABORT, 'Facebook observations are append-only'); END;
