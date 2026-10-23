# Fix for the hearing report's missing optional judge-leave tables

The iPhone diagnostic reported report_build:UndefinedTable:42P01. The cause-list
adapter also reads optional confirmed judge-leave tables; their absence must not
block display of the upstream list. This patch handles 42P01 only inside that
specific leave-read step. Missing tables from any other step still produce an
error, as do permission, network and other database errors.

Upload only services/ios_court_reports.py to the same services/ path in GitHub,
replacing the existing file. Wait for Railway deployment, then run Today's Court
Hearings again. Keep the existing token, Railway variables and Shortcut actions.
The diagnostic endpoint file and earlier court-report files remain installed.
The included tests/test_ios_missing_leave_tables.py is an optional regression
test file to upload alongside the existing test_ios_court_reports.py.

If the optional tables are absent, the report shows the complete upstream cause
list with a prominent WARNING that judge-leave verification is unavailable and
the list is unfiltered. It says NOT CHECKED and returns excluded_hearings:null,
never an invented zero verified exclusions. Confirm judge leave through the
office before acting on an unfiltered list. If the tables exist, the existing
exclusion behavior is retained. Evening reports receive the same fix.

This patch makes no schema changes and does not send messages or assign files.
It does not repair missing tables or change the morning dashboard's inherited
partial-source handling. Back up the replaced file; restore it to roll back.
No GitHub or production action was performed by the assistant.

Local validation: syntax compilation and 31 unique regression tests passed,
including six new tests reproducing missing judge-leave tables, empty results,
evening calendar behavior, existing exclusions, other database failures and
missing tables outside the leave-read step. Tests use stub connections and
synthetic data. Live deployment verification remains necessary.

If the same 42P01 diagnostic persists after uploading this file, share the latest
"iOS court report step failed" line. That would indicate a different failing
step, or that the replacement file is not running.
