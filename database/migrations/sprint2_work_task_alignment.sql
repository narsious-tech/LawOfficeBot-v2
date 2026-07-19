-- Sprint 2: Work–Task alignment support
-- Safe and idempotent. Existing tables/data are preserved.

CREATE TABLE IF NOT EXISTS work_task_alignment_audit (
    id BIGSERIAL PRIMARY KEY,
    task_id INTEGER,
    advocate_diaries_work_id TEXT,
    action TEXT NOT NULL,
    actor_telegram_user_id BIGINT,
    assigned_to TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_ad_work_pending
ON tasks (source_work_id, status)
WHERE source_type = 'advocate_diaries_work';

CREATE INDEX IF NOT EXISTS idx_work_task_alignment_audit_work
ON work_task_alignment_audit (advocate_diaries_work_id, created_at DESC);
