CREATE TABLE IF NOT EXISTS client_mobile_sync_audit (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER,
    ad_client_id TEXT,
    client_name TEXT,
    existing_mobile TEXT,
    advocate_diaries_mobile TEXT,
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS client_mobile_sync_conflict_uidx
ON client_mobile_sync_audit(
    COALESCE(ad_client_id,''),
    COALESCE(existing_mobile,''),
    COALESCE(advocate_diaries_mobile,''),
    action
)
WHERE action='CONFLICT';
