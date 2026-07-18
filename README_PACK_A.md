# Pack A — Recovered Production Modules

This pack contains production modules recovered from the source files supplied
for the working LawOfficeBot.

## Contents

### commands

- attendance.py
- attendance_reports.py
- works.py
- tasks.py
- dashboard.py
- files.py
- communication.py
- client_timeline.py
- hearing_automation.py
- ad_sync_v2.py
- ad_sync_v3.py
- ad_api_diagnostics.py
- mobile_audit.py
- mobile_update_queue.py

### services

- communication_service.py
- client_timeline.py
- hearing_automation.py
- ad_sync_v2.py
- ad_sync_v3.py
- ad_api_diagnostics.py
- mobile_audit.py
- mobile_update_queue.py

### root

- attendance_app.py

## Upload instructions

Upload the complete `commands` and `services` folders to the repository root.
Upload `attendance_app.py` beside `bot.py`.

Replace files with the same names.

Do not deploy until the remaining support-module pack is uploaded because the
current `bot.py` also imports support files such as `admin_db`,
`activity_logger`, and other infrastructure modules.

## Commit message

`Pack A: add recovered production command and service modules`
