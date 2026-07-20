# Sprint 15 — Case Intelligence Timeline & Physical-File Next Dates

## New 4:30 PM physical-file list
Every day at 4:30 PM IST the bot sends a concise list of next dates recorded during that day. Each item contains the case title, case number, and next hearing date.

Recipients:
1. Office/admin chat configured through `ADMIN_CHAT_ID` (or `TELEGRAM_ADMIN_CHAT_ID`).
2. Jimmy's linked Telegram account from `staff_accounts` (`staff_name = Jimmy`).

Jimmy must first be linked as an active staff account. The list can be tested manually with `/nextdateslist`.

The source is the authoritative `case_hearing_timeline` created during hearing completion, joined with case and live-hearing records. Duplicate same-day entries are reduced to the latest entry per case.

No migration or new environment variable is required.
