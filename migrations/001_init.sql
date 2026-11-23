-- ArchWise schema v1: pricing catalog (PRD §7.5) and evaluation audit trail (FR-39).
-- Money is NUMERIC, never floating point (ETL-05).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per ETL run (ETL-07).
CREATE TABLE IF NOT EXISTS pricing_sync_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    provider        TEXT        NOT NULL,
    region          TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'published', 'unchanged', 'rejected', 'failed')),
    source_metadata JSONB       NOT NULL DEFAULT '{}'::jsonb,
    rows_extracted  INTEGER     NOT NULL DEFAULT 0,
    rows_loaded     INTEGER     NOT NULL DEFAULT 0,
    snapshot_id     BIGINT,
    error           TEXT
);

-- Immutable raw source files, kept for audit and replay (ETL-03).
CREATE TABLE IF NOT EXISTS pricing_raw_snapshots (
    raw_id           BIGSERIAL PRIMARY KEY,
    run_id           BIGINT      NOT NULL REFERENCES pricing_sync_runs(run_id),
    provider         TEXT        NOT NULL,
    offer_code       TEXT        NOT NULL,
    region           TEXT        NOT NULL,
    source_uri       TEXT        NOT NULL,
    offer_version    TEXT,
    publication_date TIMESTAMPTZ,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256           TEXT        NOT NULL,
    bytes            BIGINT      NOT NULL,
    local_path       TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_offer ON pricing_raw_snapshots (provider, offer_code, region, fetched_at DESC);

-- A versioned set of normalized rates. Only 'published' snapshots are read by the engine (ETL-06).
CREATE TABLE IF NOT EXISTS pricing_snapshots (
    snapshot_id       BIGSERIAL PRIMARY KEY,
    run_id            BIGINT      NOT NULL REFERENCES pricing_sync_runs(run_id),
    provider          TEXT        NOT NULL,
    region            TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT        NOT NULL DEFAULT 'staged'
                      CHECK (status IN ('staged', 'published', 'rejected')),
    published_at      TIMESTAMPTZ,
    source_checksums  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    validation_report JSONB       NOT NULL DEFAULT '{}'::jsonb,
    keys_expected     INTEGER     NOT NULL DEFAULT 0,
    keys_covered      INTEGER     NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_snap_published ON pricing_snapshots (provider, region, published_at DESC)
    WHERE status = 'published';

-- Normalized provider rates (ETL-04).
CREATE TABLE IF NOT EXISTS cloud_pricing_rates (
    rate_id        BIGSERIAL PRIMARY KEY,
    snapshot_id    BIGINT        NOT NULL REFERENCES pricing_snapshots(snapshot_id),
    raw_id         BIGINT        NOT NULL REFERENCES pricing_raw_snapshots(raw_id),
    provider       TEXT          NOT NULL,
    service        TEXT          NOT NULL,      -- offer code, e.g. AWSLambda
    sku            TEXT          NOT NULL,
    rate_code      TEXT          NOT NULL,
    region         TEXT          NOT NULL,
    purchase_model TEXT          NOT NULL,      -- OnDemand
    billing_unit   TEXT          NOT NULL,
    tier_begin     NUMERIC(30,6) NOT NULL DEFAULT 0,
    tier_end       NUMERIC(30,6),               -- NULL = unbounded
    rate           NUMERIC(24,12) NOT NULL,
    currency       TEXT          NOT NULL DEFAULT 'USD',
    effective_at   TIMESTAMPTZ,
    description    TEXT,
    attributes     JSONB         NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (snapshot_id, sku, rate_code)        -- stable source key
);

-- Which rate feeds which engine price key, converted to the engine's unit.
CREATE TABLE IF NOT EXISTS engine_price_keys (
    snapshot_id  BIGINT         NOT NULL REFERENCES pricing_snapshots(snapshot_id),
    engine_key   TEXT           NOT NULL,
    rate_id      BIGINT         NOT NULL REFERENCES cloud_pricing_rates(rate_id),
    value        NUMERIC(24,12) NOT NULL,
    engine_unit  TEXT           NOT NULL,
    conversion   TEXT           NOT NULL,
    PRIMARY KEY (snapshot_id, engine_key)
);

-- Every evaluation, reproducible from inputs + rules version + pricing snapshot (FR-39, NFR-01).
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id         BIGSERIAL PRIMARY KEY,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode                  TEXT        NOT NULL,   -- known | unknown | requirements
    rules_version         TEXT        NOT NULL,
    pricing_snapshot_ids  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    input                 JSONB       NOT NULL,
    output                JSONB       NOT NULL
);

INSERT INTO schema_migrations (version) VALUES ('001_init') ON CONFLICT DO NOTHING;
