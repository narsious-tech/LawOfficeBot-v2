# Sprint 10.4 — Modern Morning Command Centre

This build upgrades `/morningdashboard` from a task-and-cause-list report into an office-wide executive briefing.

## New live sections

- Office Pulse: attendance, documents, pending communications, and receipts today.
- Documents & Systems: unclassified files, cases without Drive folders, notifications, and latest sync status.
- Needs Attention: dynamically generated operational exceptions.
- Inline Telegram controls for Hearings, Tasks, Staff, Finance, Documents, Messages, Refresh, and System.

## Resilience

All optional module queries are schema-aware. Missing tables or columns show as unavailable and do not stop the dashboard.

## Deployment

Replace these files for a minimal deployment:

- `commands/dashboard.py`
- `bot.py`

After Railway redeploys, run `/morningdashboard` and confirm:

`🧩 Build: Sprint 10.4 Modern Command Centre`

The first dashboard message should contain the inline command-centre buttons.
