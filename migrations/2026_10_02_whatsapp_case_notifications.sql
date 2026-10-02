CREATE TABLE IF NOT EXISTS whatsapp_client_consent (
    phone_number TEXT PRIMARY KEY,
    consent_status TEXT NOT NULL DEFAULT 'OPTED_OUT',
    consent_source TEXT NOT NULL,
    policy_version TEXT NOT NULL DEFAULT '2026-10',
    consented_at TIMESTAMPTZ,
    opted_out_at TIMESTAMPTZ,
    updated_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_case_notification_state (
    case_db_id INTEGER PRIMARY KEY,
    case_ref TEXT,
    case_title TEXT,
    client_name TEXT,
    phone_number TEXT,
    hearing_date DATE,
    case_status TEXT,
    purpose TEXT,
    snapshot_hash TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_case_notification_ledger (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT UNIQUE NOT NULL,
    case_db_id INTEGER,
    case_ref TEXT,
    phone_number TEXT,
    event_type TEXT NOT NULL,
    hearing_date DATE,
    template_name TEXT,
    message_body TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'PENDING',
    provider_message_id TEXT,
    provider_error TEXT,
    estimated_cost_inr NUMERIC(10,4) NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS whatsapp_case_notice_month_idx
ON whatsapp_case_notification_ledger(created_at,delivery_status);
