# Sprint 26.1 — Office Calendar and Sunday Monday-File Readiness

## Office calendar

- Monday to Friday: normal morning and evening operations.
- First, third and fifth Saturday: morning/day office open; evening office closed.
- Second and fourth Saturday: full-day holiday.
- Sunday morning: closed.
- Sunday evening office: 2:00 PM to 6:00 PM.

## Dashboard behavior

- The 9:05 AM dashboard shows a closure/session notice on Sundays and second/fourth Saturdays.
- Staff morning briefs are not sent when the morning office is closed.
- The normal 4:30 PM evening dashboard is suppressed on Saturdays and Sundays.
- The physical-file next-date dispatch is suppressed whenever the evening office is closed.

## Monday physical-file workflow

- Before a working Saturday, Friday's 4:30 PM dashboard prepares Saturday.
- On a working Saturday, Monday files are finalized at 1:00 PM.
- Before a second/fourth Saturday holiday, Monday files are finalized Friday at 4:30 PM.
- Sunday 2:00 PM: arrival checklist and file status buttons.
- Sunday 5:15 PM: unresolved-file escalation.
- Sunday 5:45 PM: final Monday readiness report.

`BROUGHT` is the only ready state. `SELECTED`, `NOT_FOUND`, and
`NEEDS_ATTENTION` remain unresolved until staff update them.

## Deployment

No new Railway variables or manual SQL migration are required. Existing
`OFFICE_GROUP_CHAT_ID` or `PHYSICAL_FILE_GROUP_CHAT_ID` configuration is reused.

After deployment, verify:

1. `/menu` includes **Evening File Plan** under Documents & Drive.
2. `/eveningdashboard` opens the correct upcoming court-date plan.
3. Existing physical-file status buttons still update files normally.
