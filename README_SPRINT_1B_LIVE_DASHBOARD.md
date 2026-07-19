# LawOfficeBot v3 — Sprint 1B Live Dashboard

## Replace only this file

Upload the included file to:

`services/dashboard_service.py`

Replace the existing Sprint 1A version. No other source file needs to be edited.

## Live values added

- Hearings Today: read from `cases.next_hearing` and `cases.hearing_date`
- Pending Works: counted from Advocate Diaries pending works
- Pending Tasks: counted from PostgreSQL `tasks`
- Staff Present: open attendance sessions for today
- Staff Total: active linked staff accounts, with `staff` fallback
- Documents Today: today's rows in `case_files`
- Notifications: unapproved attendance notifications plus overdue tasks

Appointments remain `--` until the Advocate Diaries appointment integration sprint.

## Safety characteristics

- Read-only: no table or API records are changed.
- Attendance commands and attendance source files are untouched.
- Each metric fails independently; one unavailable service cannot crash `/start`.
- A 60-second cache prevents repeated API and database requests when users tap Dashboard repeatedly.

## Deployment

1. Open the GitHub repository.
2. Open `services/dashboard_service.py`.
3. Replace it with the file from this pack.
4. Commit the change.
5. Wait for Railway deployment to complete.
6. Run `/start` or tap `🏠 Dashboard`.

No database migration and no new Railway environment variable are required.

## Expected result

All available dashboard figures will show numbers. `Appointments Today` will remain `--` in this sprint.

## Rollback

Restore the Sprint 1A `services/dashboard_service.py` file if Railway reports an import problem.
