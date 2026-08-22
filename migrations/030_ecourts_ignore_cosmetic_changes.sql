-- Retire pending eCourts changes caused only by capitalization, spacing or
-- punctuation. This never changes Office OS or Advocate Diaries case values.
UPDATE ecourts_case_changes
SET review_status='IGNORED_COSMETIC',
    reviewed_at=COALESCE(reviewed_at, NOW()),
    apply_message=COALESCE(
        apply_message,
        'Ignored automatically: cosmetic text formatting only.'
    )
WHERE review_status='PENDING'
  AND field_name IN ('purpose_name', 'court_designation', 'disposal_name')
  AND LOWER(REGEXP_REPLACE(
        COALESCE(old_value, ''), '[^[:alnum:]]+', '', 'g'
      )) = LOWER(REGEXP_REPLACE(
        COALESCE(new_value, ''), '[^[:alnum:]]+', '', 'g'
      ));
