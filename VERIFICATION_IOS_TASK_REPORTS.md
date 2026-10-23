# Office Tasks verification — 23 October 2026

Connected GitHub repository: narsious-tech/LawOfficeBot-v2.
Baseline commit: `6e048283d5bb59868ad05bb22f1a1e05cf99c5cb`.
Original replaced endpoint blob: `a25bc0f45f69b4efa4cfbd7212d522de785ee2e9`.

Sources inspected: services/ios_office_dashboard.py, commands/works.py,
commands/dashboard.py, services/ad_work_service.py,
services/ad_work_case_repair.py, and the AdvocateWeb read/login safeguards.

Implementation adds four authenticated GET routes using the existing token,
server-side owner binding, inactive-profile check and cache protections. The
existing Office Dashboard and all court-report route code remain present.
Only services/ios_office_dashboard.py is replaced. The new report service does
not import task-repair/sync/completion handlers and uses SELECT-only read-only
database access. Live works reuse the existing canonical parser and web reader.

## Executed verification

Runtime: isolated CPython 3.12.10 and Flask 3.1.2.

- All changed/new Python files compile successfully.
- 18 new task-report tests passed.
- 31 prior Office Dashboard/court report/diagnostic/missing-leave-table tests passed.
- Combined result: 49 tests, 1.372 seconds, OK.

New checks cover:

- All four new routes, bound ADMIN_USER_ID and no-store/private responses.
- Missing/wrong/malformed credentials, disabled/invalid settings, inactive owner.
- Authenticated method rejection and URL token/user/date override rejection.
- Generic 503 errors without exception-message secrets in output or new logs.
- IST calendar-day classification, naive/aware timestamps, legacy date formats,
  due_at precedence, invalid due_at fallback, missing and unrecognized dates.
- Separate overdue and due-today output; completed exclusion in the SQL predicate.
- Separate local/live counts and authoritative-ID-only linkage.
- Live next-hearing dates never become task deadlines.
- Partial source failure, explicit unavailable counts and required-local-source
  failure; deadline-only reports never fetch the live work source.
- Legitimate empty sources and full 300-local-task output without truncation.
- Actual database reader sets read-only mode, selects only hard-coded fields,
  handles optional/missing core schema and closes the connection.
- Actual live read adapter requests pending works, invokes the existing canonical
  parser and rejects unexpected final URLs and login responses.

External clients, database connections and parser results are stubbed in tests.
The production parser itself is unchanged; this does not certify current live
HTML shape or real PostgreSQL schema compatibility. Production uses existing
Asia/Kolkata ZoneInfo data; offline tests substitute a fixed IST offset.
No real credentials or confidential records were used. No live request, GitHub
mutation, deployment or production change was made by the assistant.

The prior 31 tests are unchanged and are not duplicated in this ZIP; the full
repository test suite includes them if earlier test files were uploaded.
This ZIP contains five changed/new files only: one replaced runtime module,
one new runtime module, one new test module and two new documentation files.
Original snapshots, runtimes, dependencies and caches are excluded.
