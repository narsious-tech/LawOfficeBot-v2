# Pack 2 — Activate Modular Case Commands

This pack activates the modular files already uploaded:

- `commands/new_case.py`
- `commands/find_case.py`

## Upload

Upload these two files to the repository root:

```text
case_handlers.py
activate_case_modules.py
```

## Activate

Use GitHub Codespaces, Railway shell, or a local repository terminal and run:

```bash
python activate_case_modules.py
```

The helper:

1. Creates `bot.py.before_case_modules`.
2. Adds the `case_handlers` import.
3. Registers the modular handlers immediately after the Telegram application is created.
4. Leaves the legacy handlers in place as a rollback.

## Why old handlers may remain temporarily

The modular handlers are registered first in handler group 0. For `/start`,
`/newcase`, and `/findcase`, they take precedence over the later legacy
registrations.

## Commit message

```text
Pack 2: activate modular new-case and find-case handlers
```

## Test after deployment

Run these commands in Telegram:

```text
/start
/findcase CLA-2026-9500
/newcase
```

Do not delete the backup until all three commands work.
