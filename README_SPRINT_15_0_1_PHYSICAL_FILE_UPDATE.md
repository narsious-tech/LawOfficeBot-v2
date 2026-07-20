# Sprint 15.0.1 — Physical File Update Enhancement

## Changes

- The scheduled physical-file update now runs daily at **5:00 PM IST**.
- Every entry includes the case title, case number, next hearing date, and next purpose.
- The next date and purpose are read from the same latest hearing-timeline entry recorded today.
- `/nextdateslist` produces the same enhanced report on demand.
- The report continues to be sent to the configured office/admin chat and Jimmy's linked Telegram account.

## Deployment

Replace:

- `bot.py`
- `services/case_intelligence_service.py`

No database migration or Railway environment-variable change is required.

## Test

Run:

```text
/nextdateslist
```

The automatic job is named `physical_file_next_dates_500pm`.
