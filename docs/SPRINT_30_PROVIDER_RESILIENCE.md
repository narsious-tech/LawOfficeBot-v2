# Sprint 30 — Advocate Diaries Provider Resilience

Replace the included files in the same project paths and redeploy Railway.

The patch keeps Advocate Diaries HTTP work off Telegram's event loop, retries
safe reads after transient connection resets/timeouts, and skips only the
current attendance cycle when the provider remains unavailable. Email,
WhatsApp, and other scheduled jobs continue normally.

Optional Railway tuning variables (defaults are already suitable):

```
AD_CONNECT_TIMEOUT_SECONDS=10
AD_READ_TIMEOUT_SECONDS=45
AD_API_READ_TIMEOUT_SECONDS=90
AD_READ_RETRIES=2
```

Do not set excessive retries or timeouts; provider outages should remain
bounded and must not delay the rest of the bot.
