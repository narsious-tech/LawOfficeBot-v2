-- Sprint 29: read-only WhatsApp Staff Companion identity links.
ALTER TABLE staff_accounts
ADD COLUMN IF NOT EXISTS whatsapp_phone TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS staff_accounts_whatsapp_phone_uidx
ON staff_accounts(whatsapp_phone)
WHERE whatsapp_phone IS NOT NULL;

CREATE TABLE IF NOT EXISTS whatsapp_staff_link_audit (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT,
    staff_name TEXT NOT NULL,
    whatsapp_phone TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_telegram_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
