# LawOfficeBot v3 — Sprint 1A Navigation Foundation

## What this pack adds

- Persistent two-column Telegram main menu.
- New `/start`, `/home`, and `/dashboard` landing screen.
- Central menu definitions and keyboard builder.
- Safe menu router to existing Works, Tasks, Documents, and Attendance commands.
- Placeholders for modules that will be connected in later sprints.
- Initial non-invasive role helper.
- Dashboard summary contract with placeholders (`--`) only.

## Protected modules

This pack does **not** modify:

- `commands/attendance.py`
- check-in, move-in, or check-out logic
- attendance synchronization
- Advocate Diaries synchronization
- Google Drive services
- works/tasks database schema

## Files added

- `navigation/__init__.py`
- `navigation/menu.py`
- `navigation/keyboard.py`
- `navigation/permissions.py`
- `navigation/router.py`
- `navigation/registration.py`
- `commands/home.py`
- `services/dashboard_service.py`

## File replaced

- `case_handlers.py`

The replacement adds one import and one registration call. Existing modular case handlers remain intact.

## Deployment

1. Back up the current GitHub repository or create a branch.
2. Copy all files from this pack into the repository root, preserving paths.
3. Commit and push.
4. Railway will redeploy automatically if GitHub deployment is enabled.
5. Confirm the log contains `Bot started` and no import traceback.

No database migration and no new Railway environment variable are required.

## Testing checklist

1. Send `/start` — dashboard and persistent keyboard must appear.
2. Tap `🏠 Dashboard` — dashboard must reopen.
3. Tap `📋 Works` — existing Works command must run.
4. Tap `✅ Tasks` — existing My Tasks command must run.
5. Tap `📂 Documents` — existing Files command must run.
6. Tap `🕒 Attendance` — existing Attendance command must run.
7. Independently test `/checkin`, `/movein`, and `/checkout`.
8. Tap `📆 Appointments` — safe future-module message must appear.
9. Test `/findcase` to confirm existing case search remains operational.

## Rollback

1. Restore the previous `case_handlers.py`.
2. The newly added `navigation/`, `commands/home.py`, and `services/dashboard_service.py` may remain unused or may be deleted.
3. Commit and redeploy.

## Sprint 1A limitation

Dashboard values show `--` intentionally. Sprint 1B will connect live summaries one module at a time after this navigation foundation is verified.
