# LawOfficeBot v3 — Sprint 1C Operational Home Dashboard

## What changed

The Telegram `/start`, `/home`, and `🏠 Dashboard` screen now shows four additional live operational signals:

- Urgent pending tasks
- Overdue pending tasks
- Tasks due today
- A direct reminder that `/morningdashboard` opens the full court and staff briefing

## Reliability

- All task counters are read-only.
- The code checks whether optional columns such as `priority`, `due_at`, and `deadline` exist before querying them.
- Failure of the operational task counter does not stop the dashboard.
- Existing 60-second caching remains active.
- No migration or new Railway variable is required.

## Files changed

- `services/dashboard_service.py`
- `commands/home.py`

## Deployment

Commit the two changed files to GitHub and allow Railway to redeploy. Then test:

1. `/start`
2. Tap `🏠 Dashboard`
3. `/morningdashboard`

Expected home display includes `Urgent Tasks` and `Overdue / Due Today`.
