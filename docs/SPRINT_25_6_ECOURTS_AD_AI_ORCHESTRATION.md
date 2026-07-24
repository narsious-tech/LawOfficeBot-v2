# Sprint 25.6 — eCourts, Advocate Diaries and AI Work Orchestration

## Outcome

An administrator-approved eCourts case update now triggers a safe external
Advocate Diaries date synchronization after the Office OS transaction commits.
Failure of the external service cannot roll back or lose the approved local
update; the existing Advocate Diaries retry queue records failed attempts.

Matched eCourts order PDFs are analysed through the existing Ajay AI gateway.
The system creates an administrator-reviewable work proposal containing:

- action title and details;
- priority;
- proposed due date;
- current case owner.

The proposal does not become staff work until the administrator selects
**Approve & Assign**. On approval it creates an idempotent `case_works` record
and privately notifies the linked Telegram account of the current case owner.

## Commands

- `/ecourtsreview` — approve or reject grouped eCourts case changes.
- `/syncecourtsorders` — scan order PDFs and prepare new AI proposals.
- `/ecourtswork` — review the next pending AI work proposal.
- `/ecourtsops` — open the eCourts operations desk.

## Important safeguards

- Office OS is updated only after administrator approval.
- Advocate Diaries is called only after the local database commit.
- Repeated approval clicks do not repeat a successful AD synchronization.
- Every order PDF can produce only one AI proposal.
- Every approved proposal can produce only one Office OS Work.
- AI proposals remain subject to administrator review.
- If AI is unavailable, the system proposes **Review interim order manually**
  rather than inventing directions.
- A case without an approved Office OS/CNR link cannot be updated.

## Advocate Diaries scope

This release uses the verified
`POST /hearings/add-dashboard-hearing` flow to synchronize the approved next
date and purpose. It does not make a second call merely to add AI-generated
work, because repeating that form could duplicate a hearing. The approved AI
work is stored in the Office OS and assigned to the current case owner.

## Deployment

Upload the changed files or deploy the complete package. No new Railway
variables are required. Existing variables must remain configured:

- `DATABASE_URL`
- `AD_EMAIL`
- `AD_PASSWORD`
- `AI_ENABLED=true`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `AI_ADMIN_USER_IDS`

The schema is created automatically at first use. The SQL migration is included
for controlled/manual migration workflows.

## Test

1. Run `/ecourtsreview` and approve one linked change containing last and next
   hearing dates.
2. Confirm the result shows a separate Advocate Diaries date-sync status.
3. Put a matching order PDF in the Drive `eCourts Order Inbox`.
4. Run `/syncecourtsorders`.
5. Run `/ecourtswork`.
6. Review the proposal and select **Approve & Assign**.
7. Confirm the Work appears in `/workboard` or `/myworks` for the current owner.

## Rollback

Restore the previous `commands/ecourts_backup.py`. The new service, prompt,
migration, and tables are additive and can remain in place without affecting
the earlier eCourts reconciliation workflow.
