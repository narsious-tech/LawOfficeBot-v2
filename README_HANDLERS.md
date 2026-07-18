# Sprint 2 — Telegram Handler Registry

## Delivered file

- `handlers.py`

## Purpose

This file extracts all `app.add_handler(...)` registrations from the current
monolithic `bot.py`.

It uses a migration-safe namespace bridge:

```python
register_handlers(app, globals())
```

This avoids circular imports while the command functions are still being moved
out of `bot.py`.

## Important

Uploading this file alone does not change the running bot. The next delivery
will replace the handler-registration block in `bot.py` with:

```python
from handlers import register_handlers

register_handlers(app, globals())
```

Do not manually delete the existing registrations yet.

## Commit message

`Sprint 2: add centralized Telegram handler registry`
