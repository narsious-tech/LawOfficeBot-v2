CREATE TABLE IF NOT EXISTS whatsapp_client_reception_state (
    sender_phone TEXT PRIMARY KEY,
    sender_name TEXT,
    stage TEXT NOT NULL,
    related_case_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_client_requests (
    id BIGSERIAL PRIMARY KEY,
    sender_phone TEXT NOT NULL,
    sender_name TEXT,
    request_type TEXT NOT NULL,
    request_text TEXT,
    related_case_id TEXT,
    provider_message_id TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_client_requests_provider_uidx
ON whatsapp_client_requests(provider_message_id)
WHERE provider_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS whatsapp_client_requests_status_idx
ON whatsapp_client_requests(status,created_at DESC);
