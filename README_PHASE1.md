# Law Office Bot v2 — Phase 1 Database Extraction

This bundle extracts database bootstrap SQL from the original `bot.py` into a dedicated `database` package while preserving all existing bot behaviour.

## Apply this phase

1. Copy every file and folder from the current working repository into the new `LawOfficeBot-v2` repository. Do not copy `.env` or any secret credential file.
2. Add the new `database/` folder from this bundle.
3. Replace the copied `bot.py` with the `bot.py` in this bundle.
4. Add `.gitignore`.
5. Commit with: `Phase 1: extract database initialization`.
6. Do not connect the v2 repository to the production Railway service yet.

## Railway test later

Use the same environment-variable names as production, but create a separate Railway service. Initially, point v2 to a test PostgreSQL database or a temporary Railway database.

## Expected startup log

The app should initialize the schema and then continue to the existing startup sequence. Existing commands and scheduled jobs remain unchanged in this phase.
