# Sprint 20.1 — Ajay AI Foundation

This release adds AILIP v1.0.0 to the existing Law Office OS without changing staff workflows.

See `docs/AILIP_V1_FOUNDATION.md` for configuration, deployment, testing and rollback.

Primary entry point: `/ai`

The release is intentionally private and disabled by default. Configure `AI_ADMIN_USER_IDS`, `OPENAI_API_KEY`, then set `AI_ENABLED=true` after the first safe boot.
