CREATE TABLE IF NOT EXISTS staff_bot_activity (
    id BIGSERIAL PRIMARY KEY,
    telegram_update_id BIGINT,
    event_kind TEXT NOT NULL,
    telegram_user_id BIGINT NOT NULL,
    staff_name TEXT NOT NULL,
    staff_role TEXT,
    chat_id BIGINT,
    chat_type TEXT,
    chat_title TEXT,
    summary TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notified_at TIMESTAMPTZ,
    notification_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(telegram_update_id, event_kind)
);

CREATE INDEX IF NOT EXISTS idx_staff_bot_activity_created
ON staff_bot_activity(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_staff_bot_activity_staff
ON staff_bot_activity(telegram_user_id, created_at DESC);
