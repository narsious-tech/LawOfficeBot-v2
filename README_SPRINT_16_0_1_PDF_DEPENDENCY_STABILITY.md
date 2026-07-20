# Sprint 16.0.1 - PDF Dependency Stability Patch

## Fix

Railway failed during startup because `services/printable_causelist_service.py`
imported ReportLab at module import time. If ReportLab was not installed, the
entire Telegram bot crashed before it could start.

## Changes

- Added `reportlab>=4.0,<5.0` to `requirements.txt`.
- ReportLab is now imported only inside `build_causelist_pdf()`.
- Missing ReportLab no longer prevents `bot.py` from importing or starting.
- `/printablecauselist` and the 4:30 PM evening dashboard fall back to a text
  cause list when PDF generation is temporarily unavailable.
- The normal Legal-size PDF remains the primary output once Railway installs
  the dependency.

## Deployment

Replace:

- `requirements.txt`
- `services/printable_causelist_service.py`
- `commands/evening_dashboard.py`

Commit and redeploy Railway. Railway must rebuild dependencies after the
`requirements.txt` change.

## Verification

1. Confirm Railway completes dependency installation and starts the bot.
2. Run `/printablecauselist tomorrow`.
3. Run `/eveningdashboard`.
4. Confirm a Legal-size PDF is attached. If PDF generation fails, confirm the
   bot remains online and sends the text fallback instead.
