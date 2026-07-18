# Sprint 3 — New Case Module

## Delivered files

```text
commands/
    __init__.py
    new_case.py
```

## What was migrated

The complete production `/newcase` workflow:

- Client and case intake
- Advocate Diaries client lookup
- Case type lookup
- Judge lookup
- Client type lookup
- Hearing-date normalization
- Advocate Diaries case creation
- Google Drive case-folder creation
- Local client mirroring
- Local PostgreSQL case insertion
- Transaction rollback and error reporting

## Upload instructions

Upload the entire `commands` folder to the repository root.

If GitHub asks whether to replace `commands/__init__.py`, replace it only if
you do not already have a meaningful package file there.

## Important

Do not delete the original `/newcase` functions from `bot.py` yet.

The next delivery will update `handlers.py` to import and register:

```python
from commands.new_case import build_new_case_conversation_handler
```

After that, the original new-case registration can be removed safely.

## Required existing modules

The new module expects these production files to be present later:

```text
advocate_web.py
utils/drive.py
```

It also requires the existing PostgreSQL tables created by
`database/schema.py`.

## Commit message

`Sprint 3: migrate new-case conversation`
