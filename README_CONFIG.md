# Sprint 1 — Configuration Layer

## Files

- `config.py`
- `.env.example`

## Upload instructions

1. Upload `config.py` to the repository root.
2. Upload `.env.example` to the repository root.
3. Do not upload a real `.env` file.
4. Existing code using `from config import DATABASE_URL` remains compatible.
5. No changes to `bot.py` are required for this step.

## Railway

Keep your existing Railway environment variables. Add these optional variables when convenient:

- `APP_ENV=production`
- `APP_TIMEZONE=Asia/Kolkata`
- `LOG_LEVEL=INFO`
- `DEBUG=false`

## Commit message

`Sprint 1: centralize application configuration`
