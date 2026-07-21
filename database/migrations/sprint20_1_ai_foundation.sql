-- AILIP v1.0.0 / Sprint 20.1
-- Idempotent AI foundation schema. The application also applies this safely on first /ai use.
CREATE TABLE IF NOT EXISTS ai_sessions (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    feature TEXT NOT NULL DEFAULT 'general',
    case_reference TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_user_updated ON ai_sessions (telegram_user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS ai_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_session ON ai_messages (session_id, id);
CREATE TABLE IF NOT EXISTS ai_usage (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT REFERENCES ai_sessions(id) ON DELETE SET NULL,
    telegram_user_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ai_prompt_versions (
    id BIGSERIAL PRIMARY KEY,
    prompt_name TEXT NOT NULL,
    version TEXT NOT NULL,
    checksum TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(prompt_name, version)
);
CREATE TABLE IF NOT EXISTS ai_preferences (
    telegram_user_id BIGINT PRIMARY KEY,
    response_detail TEXT NOT NULL DEFAULT 'BALANCED',
    default_feature TEXT NOT NULL DEFAULT 'general',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
