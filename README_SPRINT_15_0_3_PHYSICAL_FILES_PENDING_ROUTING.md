# Sprint 15.0.3 — Physical Files & Pending Case Routing

At 5:00 PM IST the bot now performs four coordinated deliveries:

1. **Jimmy:** compact list of cases whose next date/purpose was updated today.
2. **Office group:** combined Updated Cases and Advocate Diaries Pending Cases report.
3. **Case owners:** each owner receives only the pending cases currently assigned to them through Sprint 14 court-floor ownership.
4. **Priya:** all pending cases grouped by owner for follow-up and supervision.

The `/nextdateslist` command previews the complete combined office report.

## Railway variable

Set the Telegram group chat ID:

```text
PHYSICAL_FILE_GROUP_CHAT_ID=-100xxxxxxxxxx
```

`OFFICE_GROUP_CHAT_ID` is accepted as a fallback. Existing `ADMIN_CHAT_ID` continues to receive the complete report.

## Advocate Diaries source

Pending cases are fetched from the existing Advocate Diaries `court_cases` API and selected where `status = pending`. The report states whether next date, next purpose, or the overall Advocate Diaries update remains pending.

## Assignment fallback

Pending cases are routed using active `case_ownership`. If no ownership record exists, the case defaults to Preet, matching the office rule.
