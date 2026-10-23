# Hearing report diagnostic patch

The supplied Railway screenshot shows today-hearings returning 503 inside the
report handler, and office-dashboard returning 200. It does not identify which
report dependency failed. A separate 401 line is an unauthenticated request and
does not establish the cause of the authenticated 503 requests.

Upload these two runtime files over the installed court-reports patch, preserving
paths:

- services/ios_office_dashboard.py
- services/ios_court_reports.py

Also included: new tests/test_ios_report_diagnostics.py and this INSTALL file.
No new variables, token, dependencies, schema, or Shortcut changes are needed.
Keep backups of both files. Let your manual GitHub upload deploy to Railway.
No deployment or GitHub change was made by the assistant.

Run Today's Court Hearings once after deployment. If it fails, the existing
summary display now shows a diagnostic such as report_build:UndefinedTable:42P01.
This is an example, not the established cause of your failure.

Send the diagnostic shown on the iPhone and the new Railway lines containing
"iOS court report step" or "iOS court report retrieval failed". Logs identify
profile_read, report_import or report_build, and fetch/leave/formatter steps.
Only exception types and validated PostgreSQL SQLSTATE codes are included;
exception messages, credentials and case contents are not logged by this patch.
If the request hangs, the last "step started" line identifies the in-progress
operation. Existing upstream retries/timeouts are unchanged.

The patch preserves authentication, read-only access, successful report contents,
and 503 status on failures. It is diagnostic instrumentation, not a claimed fix
for an unconfirmed root cause. Restore the two backups to roll it back.

Local checks: Python syntax compilation and all 25 tests passed (23 prior report
and Office Dashboard regressions, plus 2 diagnostic step tests). External sources,
PostgreSQL and timezone data are stubbed in the tests. No live service was tested.
