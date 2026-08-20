# Sprint 28.2 — Private Staff Activity Feed

Every interaction by linked staff is retained in `staff_bot_activity` and sent
privately to the configured `ADMIN_USER_ID`.

Captured activity includes commands, ordinary text, document/photo/video/voice
submissions, shared locations/contacts and inline-button actions. Ajay's own
actions are excluded to prevent self-alert loops. Telegram update IDs prevent
duplicate records and alerts.

Security rules:

- `/linkstaff` credentials are always redacted.
- Alerts never fall back to `ADMIN_CHAT_ID` or the office group.
- Failed notifications remain in the database with their delivery error.

Admin commands:

- `/activitystatus`
- `/activityfeed`
- `/activityfeed 50`

Railway variables:

- `ADMIN_USER_ID=<Ajay Telegram user ID>`
- `STAFF_ACTIVITY_FEED_ENABLED=true`
