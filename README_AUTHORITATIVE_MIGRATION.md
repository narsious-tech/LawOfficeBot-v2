# Authoritative Migration Pack

This pack was built directly from the uploaded production repository
`law-office-bot-main.zip`.

## What it includes

- Patched production `bot.py`
- Exact production `commands/` modules
- Exact production `services/` modules
- `commands/admin_db.py`
- `services/activity_logger.py`
- `advocate_diaries.py`
- `api_explorer.py`
- `attendance_app.py`
- Exact production `utils/drive.py`
- Attendance HTML template
- Production `requirements.txt`
- Production `Procfile`

## Files intentionally not included

The following files already belong to the modular v2 migration and must remain
in the repository:

- `commands/new_case.py`
- `commands/find_case.py`
- `case_handlers.py`
- `database/`
- `handlers.py`
- `scheduler.py`

## Upload instructions

1. Upload all pack contents to the root of `LawOfficeBot-v2`.
2. Preserve the folder structure.
3. Replace matching files when GitHub asks.
4. Do not delete `commands/new_case.py`, `commands/find_case.py`,
   `case_handlers.py`, or the `database` folder.
5. Commit the upload.
6. Deploy to Railway.

The included `bot.py` already calls:

```python
register_case_handlers(app)
```

immediately after creating the Telegram application. Therefore, do not run
`activate_case_modules.py` after uploading this pack.

## Suggested commit message

`Sync authoritative production source and activate modular case handlers`

## Initial Telegram tests

```text
/start
/findcase <existing-case-id>
/newcase
/attendance
/works
/files
/morningdashboard
```
