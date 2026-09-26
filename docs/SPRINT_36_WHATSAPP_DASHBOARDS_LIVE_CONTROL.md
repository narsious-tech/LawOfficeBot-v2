# Sprint 36 — WhatsApp Dashboards and Live Hearing Control

## What is included

- Staff command `MORNING DASHBOARD`: private priorities and assigned-work brief.
- Staff command `EVENING DASHBOARD`: private day-closing accountability board.
- Owner command `MORNING`: full owner command-centre dashboard.
- Owner command `EVENING`: next court working day's hearing overview from Advocate Diaries.
- Weekend handling: Saturday/Sunday dashboards automatically target Monday; Friday
  also skips a second/fourth-Saturday court holiday under the shared office calendar.
- Owner command `LIVE`: paginated live-hearing board (eight hearings per page).
- Owner command `LIVE REFRESH`: refresh from Advocate Diaries, then open the board.
- Owner-only status changes: Called, Passed Over, Adjourned, Order Reserved and Reset Listed.
- Every live-status change requires a separate confirmation tap.

## Safety and cost controls

- Dashboards are on demand; this sprint does not send scheduled WhatsApp templates.
- No paid template fallback is used.
- Staff cannot access the owner live-control actions.
- `Complete Hearing` stays in Telegram because it requires the next date, purpose,
  order, documents, assignee, due date and priority.
- Physical-file selection stays in the protected Telegram evening workflow in this sprint.
- Live refresh reads Advocate Diaries; it does not enable paid eCourts case-detail scans.

## Deployment

1. Extract this ZIP over the Railway project, preserving folders.
2. Redeploy the service.
3. Confirm `WHATSAPP_STAFF_COMPANION_ENABLED=true` remains set.
4. From a linked staff number, send `MENU` and choose Morning Dashboard or Evening Dashboard.
5. From the linked owner number, send `MORNING`, `EVENING`, then `LIVE`.
6. Open a hearing, choose a status and confirm it.

No new Railway variable or database migration is required.
