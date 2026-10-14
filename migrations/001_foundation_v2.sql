-- Data Foundation V2. Applied by migrate.py inside one SQLite transaction.
-- Legacy tables are retained verbatim under legacy_* names.

ALTER TABLE Products RENAME TO legacy_products;
ALTER TABLE Sellers RENAME TO legacy_sellers;
ALTER TABLE Marketplace_Listings RENAME TO legacy_marketplace_listings;
ALTER TABLE Price_History RENAME TO legacy_price_history;
ALTER TABLE Advertising_Campaigns RENAME TO legacy_advertising_campaigns;

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    category TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(attributes_json))
);

CREATE TABLE sellers (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    source_seller_id TEXT,
    display_name TEXT,
    phone_number TEXT,
    wilaya TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (json_valid(source_payload_json)),
    UNIQUE (platform, source_seller_id)
);

CREATE TABLE marketplace_listings (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    source_listing_id TEXT,
    seller_id INTEGER REFERENCES sellers(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    raw_title TEXT NOT NULL,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('active', 'inactive', 'deleted', 'unknown')),
    first_seen_at TEXT,
    last_seen_at TEXT,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (json_valid(source_payload_json)),
    UNIQUE (platform, source_listing_id)
);

CREATE UNIQUE INDEX ux_marketplace_listing_url
    ON marketplace_listings(platform, url) WHERE url IS NOT NULL;

CREATE TABLE marketplace_listing_observations (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    price REAL CHECK (price IS NULL OR price >= 0),
    currency TEXT NOT NULL DEFAULT 'DZD',
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'deleted', 'unknown')),
    availability TEXT,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (json_valid(source_payload_json)),
    UNIQUE (listing_id, observed_at)
);

CREATE TABLE facebook_ads (
    id INTEGER PRIMARY KEY,
    library_id TEXT,
    url TEXT,
    page_source_id TEXT,
    page_name TEXT,
    platform TEXT NOT NULL DEFAULT 'facebook',
    platforms_json TEXT NOT NULL DEFAULT '["facebook"]',
    creative_text TEXT,
    creative_fingerprint TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    start_date TEXT,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('active', 'inactive', 'unknown')),
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (json_valid(platforms_json)),
    CHECK (json_valid(source_payload_json)),
    UNIQUE (platform, library_id)
);

CREATE UNIQUE INDEX ux_facebook_ads_url
    ON facebook_ads(url) WHERE url IS NOT NULL;

CREATE TABLE facebook_ad_observations (
    id INTEGER PRIMARY KEY,
    ad_id INTEGER NOT NULL REFERENCES facebook_ads(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    active INTEGER CHECK (active IN (0, 1) OR active IS NULL),
    detected_metadata_json TEXT NOT NULL DEFAULT '{}',
    creative_fingerprint TEXT,
    CHECK (json_valid(detected_metadata_json)),
    UNIQUE (ad_id, observed_at)
);

CREATE TRIGGER trg_listing_observations_no_update
BEFORE UPDATE ON marketplace_listing_observations
BEGIN
    SELECT RAISE(ABORT, 'marketplace observations are append-only');
END;

CREATE TRIGGER trg_listing_observations_no_delete
BEFORE DELETE ON marketplace_listing_observations
BEGIN
    SELECT RAISE(ABORT, 'marketplace observations are append-only');
END;

CREATE TRIGGER trg_ad_observations_no_update
BEFORE UPDATE ON facebook_ad_observations
BEGIN
    SELECT RAISE(ABORT, 'Facebook ad observations are append-only');
END;

CREATE TRIGGER trg_ad_observations_no_delete
BEFORE DELETE ON facebook_ad_observations
BEGIN
    SELECT RAISE(ABORT, 'Facebook ad observations are append-only');
END;

CREATE TABLE product_aliases (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('marketplace', 'facebook', 'manual')),
    raw_alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, source_type, normalized_alias)
);

CREATE TABLE product_mappings (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('marketplace_listing', 'facebook_ad')),
    source_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    alias_id INTEGER REFERENCES product_aliases(id) ON DELETE SET NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    mapping_source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_type, source_id, mapping_source)
);

CREATE INDEX ix_marketplace_listings_seller ON marketplace_listings(seller_id);
CREATE INDEX ix_marketplace_listings_product ON marketplace_listings(product_id);
CREATE INDEX ix_marketplace_listings_status ON marketplace_listings(status);
CREATE INDEX ix_listing_observations_time ON marketplace_listing_observations(observed_at);
CREATE INDEX ix_facebook_ads_product ON facebook_ads(product_id);
CREATE INDEX ix_facebook_ads_status ON facebook_ads(status);
CREATE INDEX ix_ad_observations_time ON facebook_ad_observations(observed_at);
CREATE INDEX ix_product_mappings_product ON product_mappings(product_id);
CREATE INDEX ix_product_alias_lookup ON product_aliases(source_type, normalized_alias);

INSERT INTO sellers (
    id, platform, display_name, phone_number, wilaya, source_payload_json
)
SELECT id, origin_platform, seller_name, phone_number, wilaya,
       json_object('legacy_seller_id', id, 'legacy_origin_platform', origin_platform)
FROM legacy_sellers;

INSERT INTO marketplace_listings (
    id, platform, source_listing_id, seller_id, product_id, raw_title, url,
    status, first_seen_at, last_seen_at, source_payload_json
)
SELECT l.id, l.platform, NULL, l.seller_id, NULL, l.raw_title, l.listing_url,
       CASE l.status WHEN 'active' THEN 'active' WHEN 'deleted' THEN 'deleted' ELSE 'unknown' END,
       NULL, l.last_seen,
       json_object('legacy_listing_id', l.id, 'legacy_product_id', l.product_id,
                   'legacy_current_price', l.current_price)
FROM legacy_marketplace_listings AS l;

INSERT INTO marketplace_listing_observations (
    listing_id, observed_at, price, currency, status, availability, source_payload_json
)
SELECT l.id, COALESCE(l.last_seen, CURRENT_TIMESTAMP),
       CASE WHEN l.current_price > 0 THEN l.current_price ELSE NULL END,
       'DZD', CASE l.status WHEN 'active' THEN 'active' WHEN 'deleted' THEN 'deleted' ELSE 'unknown' END,
       CASE l.status WHEN 'active' THEN 'listed' WHEN 'deleted' THEN 'not_listed' ELSE NULL END,
       json_object('migration', '001_foundation_v2', 'legacy_price_zero_treated_as_unknown',
                   CASE WHEN l.current_price = 0 THEN json('true') ELSE json('false') END)
FROM legacy_marketplace_listings AS l;

INSERT INTO facebook_ads (
    id, library_id, url, platform, platforms_json, first_seen_at, last_seen_at,
    start_date, status, product_id, source_payload_json
)
SELECT a.id, NULL, a.ad_url, a.platform, json_array(a.platform),
       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, a.start_date,
       CASE a.status WHEN 'active' THEN 'active' ELSE 'inactive' END,
       NULL,
       json_object('legacy_campaign_id', a.id, 'legacy_product_id', a.product_id,
                   'legacy_status', a.status, 'targeted_wilayas', a.targeted_wilayas,
                   'first_seen_semantics', 'imported_at_migration')
FROM legacy_advertising_campaigns AS a;

INSERT INTO facebook_ad_observations (
    ad_id, observed_at, active, detected_metadata_json
)
SELECT a.id, CURRENT_TIMESTAMP,
       CASE a.status WHEN 'active' THEN 1 ELSE 0 END,
       json_object('legacy_status', a.status, 'legacy_start_date', a.start_date,
                   'legacy_targeted_wilayas', a.targeted_wilayas,
                   'migration', '001_foundation_v2')
FROM legacy_advertising_campaigns AS a;
