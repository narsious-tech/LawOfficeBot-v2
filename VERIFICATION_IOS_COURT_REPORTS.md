# Verification — 23 October 2026

Repository: `narsious-tech/LawOfficeBot-v2`

Pinned baseline: `8e12437911d72fd422bb3ee349eac3e719a0b551`.
Read through the connected GitHub tools; no GitHub mutation occurred.

Existing code inspected: commands/dashboard.py, commands/evening_dashboard.py,
services/whatsapp_dashboard_service.py, services/office_calendar_service.py,
services/judge_leave_service.py, services/staff_motivation_service.py,
services/ios_office_dashboard.py, services/role_intelligence_service.py,
attendance_app.py, advocate_web.py, advocate_diaries.py.

The direct cause list reuses the bot's official PDF/API reader and its full
floor/room/judge renderer. The morning adapter invokes the existing compact
morning builder. The evening adapter reuses its calendar, cause-list source,
confirmed judge-leave filter and daily reflection while avoiding Telegram
sending, PDF attachment and physical-file-selection actions.

## Executed locally

Runtime: isolated CPython 3.12.10, Flask 3.1.2.

- Syntax compilation of all changed/new Python files: PASS.
- 15 new regression tests: PASS.
- 8 previous Office Dashboard regression tests: PASS.
- Combined suite: 23 tests, 0.728 seconds, OK.

New coverage:

- All four endpoint success responses and no-store/private cache headers.
- Owner ID binding; invalid/missing/Unicode credentials rejected before data reads.
- Disabled/malformed configuration fails closed.
- Query tokens, arbitrary dates, user-ID overrides and mutation methods rejected.
- Inactive admin denied, client and new route-log errors omit exception secrets.
- Actual bot formatter retains case number, title, purpose, floor, room and judge.
- Cause-list source selects existing PDF reader, with existing API fallback.
- Confirmed judge-leave exclusions and legitimate empty court dates.
- Full 25-case evening output, with no inherited ten-case WhatsApp limit.
- Existing office calendar selects 26 October after the fourth-Saturday closure;
  normal weekday, working Saturday and Sunday transitions also checked.
- Actual morning builder propagates read-only mode into database and leave readers.
- Actual changed database readers set read-only sessions, issue SELECT queries,
  close connections, and preserve bot defaults.
- Judge-leave reader skips ensure_schema for read-only calls and preserves its
  existing bot default behavior.
- Upstream cause-list failure propagates instead of returning empty success.

Previous regression coverage also includes import of the full existing Flask
module with unrelated integrations stubbed, health/root behavior, existing route
registration, Office Dashboard output and optional read-only role service mode.

Tests use synthetic records, mocked database connections and upstream clients.
Local timezone loading is replaced by a fixed IST offset for these tests;
production continues using the existing Asia/Kolkata ZoneInfo setting. No real
database, Railway deployment, iPhone, Advocate Diaries session or production
endpoint was contacted. Real SQL/schema compatibility and source values must
still be checked in staging. This is local verification, not live certification.

The original eight-test file is unchanged and is not duplicated in this ZIP.
The 15 new tests require the existing full repository (including its unchanged
calendar and motivation modules), as documented in INSTALL.

## Changed-files archive

Three existing files changed, four files added. No unchanged runtime modules,
secrets, dependencies, Python runtime, caches or snapshots are included.

Original GitHub blob identifiers:

| Existing file | Original blob SHA |
| --- | --- |
| commands/dashboard.py | e92ef7406ef847862e7a92e26640f0a512c802e4 |
| services/judge_leave_service.py | 3579f1a6570bc5525e8c0b2440757e33b391afef |
| services/ios_office_dashboard.py | 09b43b0a629d3417558a4645462cfbcaf9ecd327 |

Business access is read-only: database sessions reject writes; the HTTP adapters
never invoke message-sending, case mutation or file assignment workflows.
Existing upstream authentication may create a login session. The report token
grants office-wide owner read access, matching the prior integration.
