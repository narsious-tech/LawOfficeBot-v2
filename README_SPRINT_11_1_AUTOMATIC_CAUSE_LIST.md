# Sprint 11.1 — Automatic Morning Cause List

## Schedule (Asia/Kolkata)
- 08:45 — Advocate Diaries case synchronization
- 08:55 — Automatic detailed daily cause list to `OFFICE_GROUP_CHAT_ID`
- 09:05 — Sprint 11 Command Centre
- 09:10 — Individual staff morning briefs

## Delivery behaviour
- Uses the dashboard's resilient Advocate Diaries loader: authenticated PDF first, API fallback.
- Sends court-wise cause-list messages and safely splits long output below Telegram limits.
- Handles Sunday and zero-hearing days with a clean message.
- Prevents a duplicate scheduled delivery during the same bot process/day.
- Sends a diagnostic failure notice to the office group if both sources fail.
- `/testcausejob` forces an immediate test delivery even when today's scheduled list was already sent.

## Required Railway variables
- `OFFICE_GROUP_CHAT_ID`
- `AD_API`
- `AD_EMAIL`
- `AD_PASSWORD`

## Minimal deployment
Replace `bot.py`, redeploy Railway, then run `/testcausejob`.
