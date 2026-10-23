# Who's in Office — iPhone Shortcut

This is an incremental patch for your working dashboard, hearings and task APIs.
The current connected GitHub main source was inspected before preparing it.
Original `services/ios_office_dashboard.py` blob:
`c76d2dce81bc08bd94a222f97a5b5aedbafa08ad`.
The existing `/whoinoffice` handler was inspected in
`commands/attendance_reports.py` (blob
`74b61040ec088724a8c592d9c6ac91640580db0c`).

## Upload

Extract the ZIP and upload these TWO runtime files at their matching GitHub paths:

- `services/ios_office_dashboard.py` — replace the existing file; previous report
  routes are retained and the new attendance route is appended.
- `services/ios_attendance_reports.py` — new file.

Keep the `services/` directory. Do not put these files at repository root.
Back up the replaced file. If your repository has newer edits, merge the added
route instead of overwriting them. The ZIP also includes a new test file and
these instructions. Existing attendance-app, task and court-report modules stay
installed; no older ZIP needs to be uploaded again.

Commit/upload manually and wait for the new Railway deployment to become active.
No GitHub changes, deployment or production requests were made by the assistant.
No database migrations, new libraries, bot command registrations, start command
or scheduler changes are required.

## Railway variables

NO NEW VARIABLES OR TOKEN. Keep these working existing values on the same service:

| Variable | Value |
| --- | --- |
| `IOS_OFFICE_DASHBOARD_ENABLED` | `true` |
| `IOS_OFFICE_DASHBOARD_TOKEN` | Your existing private generated token |
| `ADMIN_USER_ID` | Existing owner's positive numeric Telegram user ID |
| `DATABASE_URL` | Existing attendance database connection |

The report reads only the database; it does not contact Advocate Diaries or
Google Drive and does not require new credentials. Keep the existing `PORT`
and Railway start command.

## Add the Shortcut menu option

Add a menu item named **Who's in Office**. Inside its branch, duplicate the four
actions from your working Office Tasks or Today's Court Hearings branch.

1. **URL** — replace the entire URL with the address below. Copy only the address;
   do not add quotes, backticks, spaces or a trailing slash:

   https://lawofficebot-v2-production.up.railway.app/api/ios/who-in-office

2. **Get Contents of URL** — use the URL directly above it. Method **GET**.
   Expand Headers. Keep one row: name `Authorization`, value
   `Bearer YOUR_EXISTING_TOKEN` (one space after Bearer). Use your actual token
   privately, exactly as in the working branch.
3. **Get Dictionary Value** — key `summary`, input the **Contents of URL** output
   from this branch's request.
4. **Show Result** — only the orange **Dictionary Value** output from step 3.

Keep all four actions inside this menu branch. Reselect variables after copying
so the branch does not refer to a different report's URL or response. Run the
Shortcut and choose Who's in Office. Allow your Railway domain if asked.
Do not place the token in the URL or share a token-containing Shortcut publicly.

## Report details

The report reads today's `attendance_sessions` using the IST date, matching the
existing bot attendance source. It shows:

- Staff with valid OPEN sessions: name, current recorded office, check-in time
  and elapsed time since check-in.
- Staff with valid CLOSED sessions: name, check-in/checkout times, checkout
  office and elapsed session time.
- Inconsistent records under NEEDS VERIFICATION, outside both attendance counts.
- Cancelled records counted separately and excluded from working/checked-out lists.

Current office uses the existing transfer field, falling back to check-in office
on older schemas or blank values. This is more accurate after an office move
than showing only the original check-in office. The optional field is read using
PostgreSQL to_jsonb, so the endpoint does not initialize or alter schema.

OPEN sessions must have a real check-in timestamp, no checkout, and a check-in
not in the future. CLOSED sessions must have consistent check-in/checkout times.
Missing or inconsistent records are not silently presented as staff working.
No valid records means "no attendance sessions recorded," not "everyone absent."

Naive timestamps follow the existing bot's IST convention; aware timestamps are
converted to IST. Only today's dated sessions are included; prior-day unclosed
sessions are not included in this view. The established attendance schema stores
one session per Telegram user/date, as used by the existing bot. The report does
not infer physical presence from GPS or verify that an unclosed session is fresh.
Elapsed time includes breaks/time away and is not a payroll hours calculation.

No coordinates, location-history links or Telegram IDs are returned. The report
does not check anyone in or out, approve attendance, sync records, send messages,
or create schema. Database connections are read-only and closed after SELECTs.
Missing schema or database errors return 503 with a readable summary, never a
false zero attendance count.

## Validation and troubleshooting

12 new offline tests passed, along with the prior 49 tests: 61 tests total.
All changed/new Python files compile. Coverage includes current-office transfer
fallback, aware/naive timestamps, elapsed durations, checkouts, empty data,
cancelled and inconsistent sessions, privacy exclusions, read-only SQL, connection
cleanup, owner binding, cache protection, rejected methods/query overrides and
redacted failures. Tests use synthetic data and stub connections. Live PostgreSQL,
Railway and iPhone execution have not been performed for this extension.

From the full repository root with dependencies installed:

```
python -m compileall -q services/ios_office_dashboard.py services/ios_attendance_reports.py tests/test_ios_who_in_office.py
python -m unittest discover -s tests -p "test_ios_*.py" -v
```

First compare the displayed records with `/whoinoffice` on the same IST day,
using staging where possible. A location can differ after a transfer because
the iPhone report prefers current office over check-in office. Also recheck the
working Office Tasks and hearing options after deployment.

401 means missing/wrong Authorization; 403 means inactive admin profile; 503 means
disabled/bad configuration or an unavailable database/report. Check Railway logs
for `iOS attendance report failed: type=...`. New failure logs omit raw exception
contents. To inspect an unexpected response on iPhone, temporarily show Contents
of URL directly. Keep tokens and confidential staff details out of public logs.

Rollback: restore the backed-up endpoint file and remove the new attendance
report module. No database rollback is needed. The existing ENABLED variable
disables all iOS APIs when false after redeployment.
