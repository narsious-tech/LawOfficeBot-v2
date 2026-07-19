# Sprint 12.1 — Live Hearing Control

New command: `/livehearings`

The command synchronizes today's Advocate Diaries cause list into a live operational board. Each hearing can be opened and updated with one tap: Listed, Called, Passed Over, Adjourned, Order Reserved, or Disposed. Status changes are persisted in PostgreSQL and recorded in an immutable event history table.

No manual migration is required; the two Sprint 12.1 tables are created idempotently on first use.

## Deployment
Replace/add:
- `bot.py`
- `commands/live_hearings.py`
- `services/live_hearing_service.py`

Then redeploy Railway and test `/livehearings` on a court working day.
