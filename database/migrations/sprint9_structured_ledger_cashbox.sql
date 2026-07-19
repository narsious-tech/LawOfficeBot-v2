-- Optional manual migration. The Python service also applies these changes safely.
ALTER TABLE financial_ledger ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(30);
CREATE INDEX IF NOT EXISTS idx_financial_ledger_payment_mode
ON financial_ledger(payment_mode, entry_date DESC);
