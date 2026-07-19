-- LawOfficeBot v3 Sprint 4: Case Timeline
-- Safe and idempotent. This does not modify Advocate Diaries.

CREATE TABLE IF NOT EXISTS client_timeline (
    id SERIAL PRIMARY KEY,
    client_id INTEGER,
    ad_client_id TEXT,
    case_id TEXT,
    case_number TEXT,
    event_type TEXT NOT NULL,
    event_title TEXT NOT NULL,
    event_details TEXT,
    event_status TEXT,
    event_category TEXT,
    source_type TEXT DEFAULT 'SYSTEM',
    source_id TEXT,
    created_by BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_internal BOOLEAN DEFAULT TRUE,
    metadata_json JSONB DEFAULT '{}'::jsonb
);

ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS event_status TEXT;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS event_category TEXT;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'SYSTEM';
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS source_id TEXT;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS created_by BIGINT;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS event_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS is_internal BOOLEAN DEFAULT TRUE;
ALTER TABLE client_timeline ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS client_timeline_case_number_event_at_idx
ON client_timeline (case_number, event_at DESC);

CREATE INDEX IF NOT EXISTS client_timeline_case_id_event_at_idx
ON client_timeline (case_id, event_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS client_timeline_source_unique_idx
ON client_timeline (source_type, source_id, event_type)
WHERE source_id IS NOT NULL;
