ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS provider_error TEXT;
ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS client_messages_provider_message_uidx
ON client_messages(provider_message_id)
WHERE provider_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS whatsapp_inbound_messages (
    id BIGSERIAL PRIMARY KEY,
    provider_message_id TEXT UNIQUE NOT NULL,
    sender_phone TEXT NOT NULL,
    sender_name TEXT,
    message_type TEXT NOT NULL,
    message_text TEXT,
    related_case_id TEXT,
    raw_payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS whatsapp_inbound_phone_idx
ON whatsapp_inbound_messages(sender_phone,received_at DESC);
