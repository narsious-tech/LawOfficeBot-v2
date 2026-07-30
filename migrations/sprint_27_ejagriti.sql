CREATE TABLE IF NOT EXISTS ejagriti_case_links (
    id BIGSERIAL PRIMARY KEY,
    local_case_pk BIGINT NOT NULL UNIQUE,
    filing_reference TEXT,
    ejagriti_case_number TEXT,
    commission TEXT,
    link_status TEXT NOT NULL DEFAULT 'ACTIVE',
    linked_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ejagriti_snapshots (
    id BIGSERIAL PRIMARY KEY,
    local_case_pk BIGINT NOT NULL,
    filing_reference TEXT,
    last_hearing_date DATE,
    next_hearing_date DATE,
    purpose TEXT,
    stage TEXT,
    history_count INTEGER,
    source_url TEXT,
    verified_by BIGINT,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ejagriti_date_reviews (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL UNIQUE,
    local_case_pk BIGINT NOT NULL,
    local_last_date DATE,
    local_next_date DATE,
    ejagriti_last_date DATE,
    ejagriti_next_date DATE,
    purpose TEXT,
    review_status TEXT NOT NULL DEFAULT 'PENDING',
    decision_by BIGINT,
    decision_at TIMESTAMPTZ,
    decision_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ejagriti_orders (
    id BIGSERIAL PRIMARY KEY,
    local_case_pk BIGINT NOT NULL,
    order_date DATE,
    telegram_file_id TEXT,
    filename TEXT,
    drive_file_id TEXT,
    drive_url TEXT,
    uploaded_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ejagriti_reviews_status
    ON ejagriti_date_reviews (review_status, created_at);
CREATE INDEX IF NOT EXISTS idx_ejagriti_snapshots_case
    ON ejagriti_snapshots (local_case_pk, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_ejagriti_orders_case
    ON ejagriti_orders (local_case_pk, order_date DESC);
