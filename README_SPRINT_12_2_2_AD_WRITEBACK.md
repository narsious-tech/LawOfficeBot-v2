# Sprint 12.2.2 — Advocate Diaries Write-Back

This release adds outbound synchronization after a hearing completion is saved locally.

## Behaviour

- Saves the hearing completion in PostgreSQL first.
- Finds the matching Advocate Diaries case using `/court_cases?search=CASE_NUMBER`.
- Sends the new date, purpose, order summary, and preparation note to the configured update endpoint.
- Shows separate local and remote outcomes.
- Queues failed write-backs in `advocate_diaries_sync_queue` for safe retry.
- Suppresses Telegram's harmless `Message is not modified` refresh error.

## Railway configuration

Defaults are:

- `AD_CASE_UPDATE_ENDPOINT=/court_cases/{id}`
- `AD_CASE_UPDATE_METHOD=PATCH`
- `AD_UPDATE_DATE_FIELD=next_hearing`
- `AD_UPDATE_PURPOSE_FIELD=purpose`
- `AD_UPDATE_ORDER_FIELD=order_summary`
- `AD_UPDATE_PREPARATION_FIELD=notes`

Advocate Diaries installations can use different private endpoint names or payload fields. Set the variables above to the actual endpoint used by your account. The bot never loses the local completion when remote synchronization fails.
