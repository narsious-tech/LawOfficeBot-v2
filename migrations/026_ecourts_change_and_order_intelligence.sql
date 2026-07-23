CREATE TABLE IF NOT EXISTS ecourts_case_changes (
    id BIGSERIAL PRIMARY KEY,
    sync_run_id BIGINT REFERENCES ecourts_backup_sync_runs(id),
    cino TEXT NOT NULL,
    local_case_pk TEXT,
    display_case_number TEXT,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    severity TEXT NOT NULL DEFAULT 'INFO',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    alerted_at TIMESTAMPTZ,
    UNIQUE(sync_run_id, cino, field_name)
);

CREATE INDEX IF NOT EXISTS idx_ecourts_changes_detected
ON ecourts_case_changes(detected_at DESC);

CREATE TABLE IF NOT EXISTS ecourts_order_inbox (
    id BIGSERIAL PRIMARY KEY,
    drive_file_id TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    original_link TEXT,
    modified_time TIMESTAMPTZ,
    sha256_hash TEXT,
    cino TEXT,
    local_case_pk TEXT,
    case_number TEXT,
    order_date DATE,
    processing_status TEXT NOT NULL DEFAULT 'NEW',
    importance TEXT NOT NULL DEFAULT 'NORMAL',
    extracted_text TEXT,
    ai_summary TEXT,
    archived_drive_file_id TEXT,
    archived_drive_link TEXT,
    error_message TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    alerted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ecourts_order_status
ON ecourts_order_inbox(processing_status, id DESC);
