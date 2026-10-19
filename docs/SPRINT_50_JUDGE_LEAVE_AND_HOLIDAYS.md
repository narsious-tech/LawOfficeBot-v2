# Sprint 50 — Judge Leave and Guarded Holiday Greetings

## Judge-leave workflow

- Authorized uploaders: Ajay, Priya, Preet and Isha only.
- Telegram: open a private bot chat, send `/judgeleave`, then upload the official PDF.
- WhatsApp: send the official PDF directly to the office bot from an authorized linked number.
- The PDF must contain searchable text. A scanned photograph PDF is rejected safely.
- The bot compares the PDF text only with judges already present in the selected court day's Advocate Diaries cause list.
- A preview lists every matched judge and affected case. Nothing is excluded until the uploader confirms.
- Confirmed leave removes those matters from operational morning, hearing, live-control and physical-file lists only.
- Case dates, Advocate Diaries, the database case record and the official source PDF are not changed.

Optional Telegram date override (today through the next seven days):

```text
/judgeleave 20-10-2026
```

## Alerts

- Normal evening file workflow: judge-leave alert at 4:00 PM IST, file dashboard at 4:30 PM IST.
- Working-Saturday Monday finalization: alert at 12:30 PM IST, file dashboard at 1:00 PM IST.
- Telegram alerts go privately to Ajay, Priya, Preet and Isha.
- WhatsApp alerts use the free 24-hour service window. Staff outside that window are queued until their next inbound message. No paid template is used.

## Holiday greetings

- India/Punjab public and festival dates are detected using the `holidays` package.
- `COURT_HOLIDAY_SUBDIV` defaults to `PB` and can be changed, for example to `CH`.
- Annual court-calendar corrections can be supplied without a code change:

```text
COURT_HOLIDAY_OVERRIDES={"2026-11-24":"Guru Nanak Jayanti","2026-12-25":"Christmas"}
```

- A greeting is sent only if the detected holiday also has 0, 1 or 2 active listed matters after confirmed judge-leave exclusions.
- Three or more matters suppress the greeting, preventing the bot from treating a working court day as a holiday.

## Deployment

Replace the included files preserving folders, deploy Railway, and confirm the startup log shows `Bot started`.
No manual SQL migration is required; the feature creates its tables safely on first use.
