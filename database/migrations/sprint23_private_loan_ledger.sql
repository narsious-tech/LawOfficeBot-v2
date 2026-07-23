-- Sprint 23: administrator-only private loan ledger.
-- The application runs the equivalent idempotent migration automatically.

CREATE TABLE IF NOT EXISTS private_loans (
    id BIGSERIAL PRIMARY KEY,
    account_number TEXT UNIQUE,
    borrower_name TEXT NOT NULL,
    borrower_phone TEXT,
    borrower_address TEXT,
    principal_amount NUMERIC(16,2) NOT NULL CHECK (principal_amount > 0),
    outstanding_principal NUMERIC(16,2) NOT NULL CHECK (outstanding_principal >= 0),
    monthly_interest_rate NUMERIC(9,4) NOT NULL CHECK (monthly_interest_rate > 0),
    calculation_method TEXT NOT NULL DEFAULT 'REDUCING_BALANCE',
    interest_timing TEXT NOT NULL DEFAULT 'MONTHLY_IN_ADVANCE',
    loan_date DATE NOT NULL,
    next_interest_due_date DATE NOT NULL,
    maturity_date DATE,
    guarantor_name TEXT,
    guarantor_phone TEXT,
    guarantor_address TEXT,
    security_details TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','CLOSED','DEFAULTED')),
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS private_loan_transactions (
    id BIGSERIAL PRIMARY KEY,
    loan_id BIGINT NOT NULL REFERENCES private_loans(id),
    transaction_date DATE NOT NULL DEFAULT CURRENT_DATE,
    transaction_type TEXT NOT NULL
        CHECK (transaction_type IN ('DISBURSEMENT','INTEREST_RECEIVED','PRINCIPAL_RECEIVED','CHARGE')),
    amount NUMERIC(16,2) NOT NULL CHECK (amount > 0),
    payment_mode TEXT,
    reference_note TEXT,
    principal_before NUMERIC(16,2),
    principal_after NUMERIC(16,2),
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS private_loan_documents (
    id BIGSERIAL PRIMARY KEY,
    loan_id BIGINT NOT NULL REFERENCES private_loans(id),
    document_name TEXT NOT NULL,
    document_details TEXT,
    received_date DATE NOT NULL DEFAULT CURRENT_DATE,
    original_received BOOLEAN NOT NULL DEFAULT FALSE,
    drive_link TEXT,
    returned_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS private_loan_audit (
    id BIGSERIAL PRIMARY KEY,
    loan_id BIGINT REFERENCES private_loans(id),
    action TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS private_loan_reminder_log (
    id BIGSERIAL PRIMARY KEY,
    loan_id BIGINT NOT NULL REFERENCES private_loans(id),
    due_date DATE NOT NULL,
    alert_date DATE NOT NULL,
    alert_kind TEXT NOT NULL,
    sent_to BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(loan_id, due_date, alert_date, alert_kind, sent_to)
);

CREATE INDEX IF NOT EXISTS private_loans_status_due_idx
    ON private_loans(status,next_interest_due_date);
CREATE INDEX IF NOT EXISTS private_loan_txn_loan_date_idx
    ON private_loan_transactions(loan_id,transaction_date DESC,id DESC);
CREATE INDEX IF NOT EXISTS private_loan_docs_loan_idx
    ON private_loan_documents(loan_id,id DESC);
