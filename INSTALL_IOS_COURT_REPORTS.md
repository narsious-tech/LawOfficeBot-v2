# Direct court reports on iPhone

This is an incremental patch for the Office Dashboard integration you already
installed. It was prepared from the connected GitHub repository
`narsious-tech/LawOfficeBot-v2`, pinned commit
`8e12437911d72fd422bb3ee349eac3e719a0b551`.
No GitHub writes, production requests, deployment or Telegram messages were made.

## Upload these files

Extract the ZIP and upload the files at their existing relative paths, using
GitHub's Add file > Upload files from the repository root. Preserve the
`commands/`, `services/` and `tests/` folders; do not flatten them.

Runtime files:

| Path | Change |
| --- | --- |
| `commands/dashboard.py` | Existing report readers gain optional read-only mode |
| `services/judge_leave_service.py` | Existing leave reader gains optional read-only mode, skipping schema creation |
| `services/ios_office_dashboard.py` | Existing auth reused for three new routes; Office Dashboard retained |
| `services/ios_court_reports.py` | New adapters for direct morning/evening/cause-list text |

The ZIP also contains the new `tests/test_ios_court_reports.py`, this INSTALL
file, and `VERIFICATION_IOS_COURT_REPORTS.md`.

Back up the three existing runtime files first. If your branch has moved beyond
the pinned commit, compare/merge rather than overwriting newer work.
Do not re-upload the older Office Dashboard ZIP over this patch afterward.
The already-installed `attendance_app.py` registration and read-only
`services/role_intelligence_service.py` are prerequisites and are unchanged.
No new dependencies, database migrations, bot command registrations, Railway
start command or scheduler changes are needed. Existing repository modules
`services/office_calendar_service.py` and `services/staff_motivation_service.py`
are reused and are deliberately not included in this changed-files ZIP.

## Railway variables

**Keep the same values that now make your Office Dashboard work. No new token
or variables are required.** On the same Railway service:

| Variable | Exact setting |
| --- | --- |
| `IOS_OFFICE_DASHBOARD_ENABLED` | `true` |
| `IOS_OFFICE_DASHBOARD_TOKEN` | Your existing generated 43-character URL-safe token; no quotes/spaces |
| `ADMIN_USER_ID` | Your existing owner's positive numeric Telegram user ID |

Keep `DATABASE_URL`, the existing Advocate Diaries web settings and `AD_EMAIL`,
`AD_PASSWORD`, `AD_API` unchanged. `AD_API` is used by the bot's existing API
fallback if PDF reading fails; do not invent a different URL. Keep the existing
`PORT`, database schema and start command. No additional OpenAI key is required.

Upload/commit manually and wait for Railway deployment to succeed. Prefer a
staging check before deploying to production. These files do not deploy anything
by themselves. Do not run a second Telegram polling bot for testing.

## Connect your existing Shortcut menu options

Your Office Dashboard option already has the right four actions. Duplicate
those four actions into each relevant menu branch. Keep every branch's actions
inside that branch, before the next menu item/End Menu. Remove the old website
URL/Open URL actions from the report branches.

Use this URL for each option:

| Menu option | URL |
| --- | --- |
| Today's Court Hearings | `https://lawofficebot-v2-production.up.railway.app/api/ios/today-hearings` |
| Daily/Morning Dashboard | `https://lawofficebot-v2-production.up.railway.app/api/ios/morning-dashboard` |
| Evening Dashboard | `https://lawofficebot-v2-production.up.railway.app/api/ios/evening-dashboard` |
| Office Dashboard (existing) | `https://lawofficebot-v2-production.up.railway.app/api/ios/office-dashboard` |

For each branch, the exact order is:

1. **URL** — enter the URL from the table.
2. **Get Contents of URL** — select that branch's URL; Method **GET**.
   Expand Headers. Keep ONE header row: left field `Authorization`, right field
   `Bearer YOUR_EXISTING_TOKEN` with one space after Bearer. Neither the words
   "Header name" nor "Header value" should be entered as header names.
3. **Get Dictionary Value** — key `summary`; input is the output of that branch's
   **Get Contents of URL**, not a previous branch or the action's own output.
4. **Show Result** — only the orange **Dictionary Value** output of step 3.

Allow access to your Railway domain if prompted. Run the Shortcut, choose the
report menu option, and check the report's court date. There is no Safari login
or website opening step. Protect the Shortcut because it contains an office-wide
read credential. Never upload it publicly with the token in it.

### Avoid blank results on errors

The simple four actions work on success. For a better error display, insert
**Get Dictionary Value** with key `ok` after the request, followed by **If** that
value is true. Inside If, extract `summary` from the original request output and
show it. In Otherwise, **Show Result** with the original **Contents of URL**.
This makes errors visible rather than asking for a missing summary field.

For troubleshooting, temporarily show **Contents of URL** directly, exactly as
you did when checking Office Dashboard. Do not put `summary` in Headers.

## What these reports show

- **Today's Court Hearings:** full floor/room/judge cause list for today's
  Asia/Kolkata date. Case number, title and purpose use the bot's existing
  reader/parser and formatter. No ten-case limit or character truncation is added.
- **Morning Dashboard:** the existing compact LAW OFFICE COMMAND CENTRE report,
  generated on demand with its office pulse, priorities and source-health status.
  The separate Today's Court Hearings option supplies the full list.
- **Evening Dashboard:** next court-date preparation list, hearing count, confirmed
  judge-leave exclusions, calendar heading, evening reflection and the full list.
  It is a read-only view of the bot's evening preparation data, not a replay of
  all Telegram messages or its interactive file-selection controls.

Cause-list retrieval reuses `commands.dashboard.fetch_advocate_diaries_cause_groups`:
official Advocate Diaries PDF first, existing daily-cause-list API fallback.
Both direct court reports apply the bot's existing confirmed judge-leave rule.
The evening date comes from `manual_evening_plan`, including Saturday/Sunday and
Monday-finalization rules; it can be later than calendar tomorrow. Always read
the displayed Court date. No new holiday rules have been invented.

Court-list text deliberately omits optional local client/task/Drive enrichment;
it does not invent responsible staff or fetch unrelated private records. Physical
file selection, assignment, live status updates and sending reports remain in
the existing Telegram workflows. No PDF attachment is returned by these JSON
routes; the cause list is shown directly as readable text in Shortcuts.

All data is generated when requested; this does not read cached Telegram messages
or automatically run at the bot's scheduled times. Upstream authentication can
create a login session, but no business records are changed. Database reads use
PostgreSQL read-only sessions and do not initialize schema. Existing bot callers
retain their default behavior. Missing judge-leave tables/read access cause
court-report failure rather than silently ignoring exclusions. Initialize via
the established bot setup, never through an iPhone request.

## Verification and limitations

From the full repository root with its dependencies installed:

```
python -m compileall -q commands/dashboard.py services/judge_leave_service.py services/ios_office_dashboard.py services/ios_court_reports.py tests/test_ios_court_reports.py
python -m unittest discover -s tests -p "test_ios_*.py" -v
```

Local syntax checks and 23 tests passed, including 15 new tests and the previous
8 Office Dashboard regression tests. See VERIFICATION for scope. Tests use fake
records and stub external services. Railway, real PostgreSQL and iPhone behavior
have not been live-tested for this extension.

After deploying, compare Today's Court Hearings against the existing Telegram
morning list and Evening Dashboard against `/eveningdashboard` on the same date.
Use an existing report rather than invoking a Telegram send workflow solely for
comparison. Confirm exclusions and Monday transitions. Today's empty list is a
successful report; upstream failure returns 503 instead of an empty success.
The morning report can still show partial data and Unavailable source status,
as the existing bot builder does. `ok: true` there means the report was built,
not that every upstream source is healthy.

The existing PDF/API reader retries can take a while or exceed the iPhone's
network timeout during upstream trouble. No global retry/timeouts were changed.
Responses use no-store caching; there is no automatic background widget refresh.

401 = check Authorization header/token. 403 = admin profile inactive.
503 = feature/configuration invalid, missing prerequisite code/schema, database
failure or report source unavailable. Check Railway logs for
`iOS court report retrieval failed: today`, `morning`, or `evening`.
These new error logs omit raw exception details and credentials.
GET only; authenticated HEAD/OPTIONS/write methods return 405. Query parameters,
including user/date overrides and URL tokens, return 400.

Rollback: restore the three backed-up runtime files and remove the new
`services/ios_court_reports.py`. No database rollback is necessary. Setting
`IOS_OFFICE_DASHBOARD_ENABLED=false` and redeploying disables all four APIs.
