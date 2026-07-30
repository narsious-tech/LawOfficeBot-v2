# Sprint 27.0.1 — e-Jagriti Link Fix

## Fixed

`/ejagritilink` no longer fails with `KeyError: 0` while inspecting the Office OS case table.

The database metadata helper now supports both tuple-based PostgreSQL rows and named rows returned by `RealDictCursor`.

## Minimal deployment

Replace:

```text
services/ejagriti_service.py
```

No database migration or new Railway variable is required.

## Test

Run the same `/ejagritilink` command again. A valid case should now return a successful consumer-case link confirmation instead of `KeyError: 0`.
