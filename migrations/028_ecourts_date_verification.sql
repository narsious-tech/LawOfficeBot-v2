CREATE TABLE IF NOT EXISTS ecourts_date_verifications (
    id BIGSERIAL PRIMARY KEY,
    local_case_pk TEXT NOT NULL,
    cino TEXT NOT NULL,
    display_case_number TEXT,
    staff_next_date DATE,
    ecourts_next_date DATE,
    staff_last_date DATE,
    ecourts_last_date DATE,
    ecourts_purpose TEXT,
    source_sync_run_id BIGINT,
    verification_status TEXT NOT NULL,
    status_message TEXT,
    alert_sent_at TIMESTAMPTZ,
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ,
    review_decision TEXT,
    ad_sync_status TEXT,
    ad_sync_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(local_case_pk, cino)
);

ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_date_source TEXT;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_date_verification_status TEXT;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_date_verified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_ecourts_date_verification_pending
ON ecourts_date_verifications(verification_status, alert_sent_at);
