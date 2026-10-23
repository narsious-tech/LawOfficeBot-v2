# Office Tasks and Overdue / Due Today

Incremental patch for the working iPhone Office Dashboard / Court Reports
integration in `narsious-tech/LawOfficeBot-v2`.
Baseline inspected: `6e048283d5bb59868ad05bb22f1a1e05cf99c5cb`.
No GitHub writes, production requests, deployment or bot messages were performed.

## 1. Upload the files

Extract the ZIP. Upload these TWO runtime files into their matching GitHub paths:

- `services/ios_office_dashboard.py` — replaces the existing registration module;
  preserves all four earlier routes and adds four task-report routes.
- `services/ios_task_reports.py` — new read-only report module.

Do not place them at the repository root; keep the `services/` directory.
The ZIP also includes the new `tests/test_ios_task_reports.py`, this INSTALL file,
and `VERIFICATION_IOS_TASK_REPORTS.md`. These are optional to deploy but useful
to keep in the repository. Back up the replaced file. If your branch moved beyond
the pinned baseline, merge rather than overwrite newer changes.

Wait for your manual upload/commit to deploy successfully in Railway. Keep the
same start command and one intended Telegram polling instance. No requirements,
schema migrations, scheduling or bot command registrations change. The installed
court-report module (including its missing-leave-table fix) is unchanged and
not included in this ZIP. Do not re-upload the older ZIPs over this one.

## 2. Railway variables

NO NEW VARIABLES OR TOKEN ARE REQUIRED. Keep the working values:

| Variable | Setting |
| --- | --- |
| `IOS_OFFICE_DASHBOARD_ENABLED` | `true` |
| `IOS_OFFICE_DASHBOARD_TOKEN` | Your existing generated private token |
| `ADMIN_USER_ID` | Existing owner's positive Telegram user ID |
| `DATABASE_URL` | Existing PostgreSQL connection; local task source |

Keep your existing Advocate Diaries configuration, including `AD_EMAIL` and
`AD_PASSWORD`, unchanged. The live works source uses the existing AdvocateWeb
authenticated web reader, not a new service. Keep `PORT` and all other existing
variables unchanged. No new OpenAI or third-party API key is needed.

## 3. Office Tasks in your Shortcut

Inside the existing **Office Tasks** menu branch, remove **Open Reminders** and
replace it with the four actions you already use for the working hearing report:

1. **URL** — paste only the address below, without quotes/backticks or spaces:

   https://lawofficebot-v2-production.up.railway.app/api/ios/office-tasks

2. **Get Contents of URL** — select the URL directly above it. Method **GET**.
   Expand Headers: ONE row, left `Authorization`, right
   `Bearer YOUR_EXISTING_TOKEN` with one space after Bearer. Use your actual
   existing token privately; do not include the words YOUR_EXISTING_TOKEN.
3. **Get Dictionary Value** — key `summary`, input the **Contents of URL** from
   this branch's request.
4. **Show Result** — select only the orange **Dictionary Value** output.

Keep the four actions inside the Office Tasks branch, before the next menu
branch. When duplicating actions, reselect each input from its own branch so it
does not keep a reference to a previous branch's URL or response.

## 4. Add Overdue / Due Today

Add a menu option named **Overdue / Due Today**. Duplicate the same four actions
inside that branch and change only the URL to:

https://lawofficebot-v2-production.up.railway.app/api/ios/task-deadlines

It shows both sections in one report. If you prefer separate menu options, the
same code also supports these URLs:

| Option | URL |
| --- | --- |
| Overdue Tasks | `https://lawofficebot-v2-production.up.railway.app/api/ios/overdue-tasks` |
| Due Today | `https://lawofficebot-v2-production.up.railway.app/api/ios/due-today-tasks` |

Run each branch and allow domain access if prompted. Do not open these URLs in
Safari to fetch reports: Safari does not supply the private Authorization header.
No token goes into a URL. Do not share your Shortcut publicly with the token in it.

All new source-failure responses contain a readable `summary`, so your current
Show Dictionary Value setup displays the error instead of a blank result. To
inspect unexpected output, temporarily show **Contents of URL** directly.

## Report semantics

**Office Tasks** shows two clearly separated sections:

- Pending LOCAL tasks from `public.tasks`, including ID, staff, case number,
  work text, priority, deadline and imported/manual source label.
- Pending LIVE Advocate Diaries works using the bot's existing web reader and
  canonical `parse_works_html` parser. Includes work ID, client, case number,
  title and description. Pending local task IDs/assignees are linked only by
  authoritative `source_work_id` and `source_type='advocate_diaries_work'`.

One live work may also appear as one or more imported local tasks. These source
counts are never summed, and similar descriptions/case numbers are never used
to guess identity. Live source work deadlines are not supplied by the existing
parser, so none are invented. A case's next-hearing date is not a work deadline.
No repair, synchronization, assignment or completion handler is invoked.

**Overdue / Due Today** and the separate views read LOCAL tasks only. They do not
make an Advocate Diaries request. Due dates use valid `due_at` first, then the
legacy `deadline` field. Date-only and supported legacy date strings are accepted;
invalid/missing deadlines are counted explicitly and excluded from both date
buckets. Naive timestamps/date strings follow the bot's existing IST convention;
aware timestamps are converted to IST. Dates use Asia/Kolkata calendar days:

- Overdue: deadline date earlier than today.
- Due today: deadline date equals today, including a time earlier today.
- Future: later than today, counted but omitted from the deadline detail lists.

This is a calendar-day report, not a minute-by-minute overdue alarm. Completed,
closed and other non-PENDING tasks are excluded by the database query. A NULL
task status follows the existing dashboard's PENDING default. All results are
ordered by priority and then usable deadline. No silent row/character truncation
is added. Large reports can take time to render in Shortcuts.

Local task counts may differ from the older **Office Dashboard**: that service
uses `case_works` for some local figures, while these reports use `tasks`, matching
the bot's task lists. Live work counts and local task counts must not be treated
as one reconciled total.

If one Office Tasks source fails, the other can still display, with an explicit
SOURCE WARNING and `partial:true`. Unavailable counts are JSON null, not zero.
If both sources fail, or the local source needed for a deadline report fails,
the endpoint returns 503 with a readable error summary. A legitimate empty
available source returns zero and a no-pending-tasks message.

The local reader discovers optional columns but requires the core task schema
(`id`, `task`, `status`). Missing core schema is reported as unavailable. It sets
PostgreSQL read-only mode, performs SELECTs and closes its connection. It never
creates tables or repairs records. Upstream login/session activity is still
required for reading live works. Unexpected final web URLs, login pages and
unrecognized empty HTML are reported unavailable rather than a false empty list.

## Local validation and staging checks

From the full repository root with its existing dependencies installed:

```
python -m compileall -q services/ios_office_dashboard.py services/ios_task_reports.py tests/test_ios_task_reports.py
python -m unittest discover -s tests -p "test_ios_*.py" -v
```

49 offline tests passed in the prepared local checkout: 18 new task tests and
31 previous dashboard/hearing tests. See VERIFICATION for details. Real database,
Advocate Diaries HTML, Railway and iPhone execution have not been tested for
these new routes. First compare against existing bot task/work reports using
staging where possible. Do not trigger a repair/sync handler merely to make a
read-only test pass. Existing optional dependencies, including BeautifulSoup and
lxml, are already in repository requirements; no additional installation needed.

Check Office Tasks, Overdue / Due Today, then the existing Office Dashboard and
Today's Court Hearings after deployment. Compare on the same IST date. Verify
due-today, overdue, undated and completed examples. Do not paste credentials or
full confidential case lists into public logs.

401: missing/wrong Authorization. 403: admin profile inactive. 503: disabled/bad
configuration, a required source unavailable or unexpected report failure. New
application logs use `iOS task source failed` / `iOS task report failed` with
source labels and exception types, never raw exception details. Existing source
retry policies are unchanged, so live Advocate Diaries delays can still cause
client network timeouts.

Rollback: restore your backed-up `services/ios_office_dashboard.py` and remove
`services/ios_task_reports.py`. No database rollback is needed. Setting the
existing ENABLED variable to false and redeploying disables all iOS APIs.
