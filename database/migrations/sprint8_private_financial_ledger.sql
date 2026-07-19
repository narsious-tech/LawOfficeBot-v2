CREATE TABLE IF NOT EXISTS financial_ledger (
    id BIGSERIAL PRIMARY KEY,
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    entry_type VARCHAR(12) NOT NULL CHECK (entry_type IN ('INCOME','EXPENSE')),
    scope VARCHAR(20) NOT NULL CHECK (scope IN ('PERSONAL','PROFESSIONAL','STAFF')),
    category VARCHAR(80) NOT NULL,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    description TEXT NOT NULL,
    case_id BIGINT,
    case_number TEXT,
    staff_name TEXT,
    payment_mode VARCHAR(30),
    created_by_telegram_id BIGINT NOT NULL,
    created_by_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by_telegram_id BIGINT
);
CREATE INDEX IF NOT EXISTS idx_financial_ledger_date ON financial_ledger(entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_financial_ledger_case ON financial_ledger(case_number);
CREATE INDEX IF NOT EXISTS idx_financial_ledger_active ON financial_ledger(is_deleted, entry_date DESC);
