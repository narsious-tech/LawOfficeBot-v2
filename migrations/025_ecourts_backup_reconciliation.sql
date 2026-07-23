-- Sprint 25.1: eCourts backup import, reconciliation and audit history.
-- The application also runs these statements idempotently at startup/on first use.
CREATE TABLE IF NOT EXISTS ecourts_backup_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    district_count INTEGER NOT NULL DEFAULT 0,
    high_court_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    possible_count INTEGER NOT NULL DEFAULT 0,
    office_only_count INTEGER NOT NULL DEFAULT 0,
    backup_only_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    triggered_by BIGINT
);

CREATE TABLE IF NOT EXISTS ecourts_backup_records (
    cino TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    case_type TEXT,
    registration_number TEXT,
    registration_year INTEGER,
    display_case_number TEXT,
    raw_case_number TEXT,
    petitioner_name TEXT,
    respondent_name TEXT,
    establishment_name TEXT,
    establishment_code TEXT,
    state_name TEXT,
    district_name TEXT,
    court_designation TEXT,
    last_hearing_date DATE,
    next_hearing_date DATE,
    decision_date DATE,
    purpose_name TEXT,
    disposal_name TEXT,
    note TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sync_run_id BIGINT REFERENCES ecourts_backup_sync_runs(id)
);

CREATE TABLE IF NOT EXISTS ecourts_case_links (
    id BIGSERIAL PRIMARY KEY,
    local_case_pk TEXT NOT NULL UNIQUE,
    local_case_number TEXT,
    cino TEXT NOT NULL UNIQUE REFERENCES ecourts_backup_records(cino),
    match_method TEXT NOT NULL,
    confidence NUMERIC(6,5) NOT NULL DEFAULT 1,
    link_status TEXT NOT NULL DEFAULT 'APPROVED',
    approved_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ecourts_reconciliation_audit (
    id BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    local_case_pk TEXT,
    cino TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cases ADD COLUMN IF NOT EXISTS ecourts_cnr TEXT;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS ecourts_last_synced_at TIMESTAMPTZ;
