CREATE TABLE IF NOT EXISTS ecourts_ad_sync_events (
    id BIGSERIAL PRIMARY KEY,
    preparation_queue_id BIGINT NOT NULL UNIQUE,
    local_case_pk TEXT NOT NULL,
    cino TEXT NOT NULL,
    case_number TEXT NOT NULL,
    hearing_date DATE,
    next_hearing_date DATE,
    next_purpose TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    message TEXT,
    remote_case_id TEXT,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ecourts_ai_work_proposals (
    id BIGSERIAL PRIMARY KEY,
    order_inbox_id BIGINT NOT NULL UNIQUE,
    local_case_pk TEXT NOT NULL,
    cino TEXT,
    case_number TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT,
    priority TEXT NOT NULL DEFAULT 'NORMAL',
    due_date DATE,
    proposal_status TEXT NOT NULL DEFAULT 'PENDING_ADMIN',
    generation_mode TEXT NOT NULL DEFAULT 'AI',
    ai_raw_response TEXT,
    case_work_id BIGINT,
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE case_works ADD COLUMN IF NOT EXISTS external_source_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_case_works_external_source
ON case_works(source, external_source_id)
WHERE external_source_id IS NOT NULL;
