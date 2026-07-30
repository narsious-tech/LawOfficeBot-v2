# Sprint 26.3.5 — Historical Replay Guard

Urgent safety release for the eCourts-to-Advocate Diaries date recovery job.

## Protection added

- Automatic retries require an explicit `ACCEPT_ECOURTS` administrator decision.
- The verification record must be marked `ECOURTS_ACCEPTED`.
- Only decisions and failed hand-offs from the last seven days are retryable.
- A next hearing date earlier than the current India date is always blocked.
- Safety is checked twice: in the retry query and immediately before the remote write.
- Blocked historical records receive a terminal `HISTORICAL_SKIPPED` status and are
  not selected again by the recovery job.

## Minimal deployment

Replace:

`services/ecourts_orchestration_service.py`

No migration or Railway variable is required.

## Important recovery note

This release stops further historical replay. It does not automatically delete hearing
entries already written to Advocate Diaries, because deleting them without confirming
the correct prior state could remove legitimate history. Those entries must be reviewed
and corrected in Advocate Diaries.
