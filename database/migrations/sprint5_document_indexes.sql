-- Sprint 5: optional read-performance indexes. Safe and idempotent.
CREATE INDEX IF NOT EXISTS idx_case_files_case_id_lower
    ON case_files (LOWER(TRIM(case_id)));

CREATE INDEX IF NOT EXISTS idx_case_files_case_category_uploaded
    ON case_files (LOWER(TRIM(case_id)), UPPER(COALESCE(category, 'MISCELLANEOUS')), uploaded_at DESC);
