# Sprint 22 — AI Hearing Intelligence

## Scope

This release activates the private **Hearing Intelligence** option inside `/ai`.
It does not alter staff menus, automatic cause-list delivery, live-hearing control,
or the existing Office OS workflow.

## Use

1. Open `/ai`.
2. Select **Hearing Intelligence**.
3. Choose **Today** or **Tomorrow**.

Ajay AI loads matching hearings from the local master-case records and prepares a
grounded, court-wise preparation brief. Each matter remains isolated from every
other matter. Optional sources such as works, timeline, documents, ownership, and
physical-file status are loaded independently and disclosed when unavailable.

## Safety boundaries

- Google Drive folder contents are not inspected merely because a folder link exists.
- The brief does not invent facts, orders, pleadings, arguments, law, or listing times.
- An empty local result is not represented as proof that Advocate Diaries has no hearing.
- The output is an advocate working note and must be checked against the cause list,
  physical/digital file, orders, and current law.

## Deployment

Replace the three changed source files and redeploy Railway. No database migration
or new environment variable is required.

## Verification

Run `/ai`, open **Hearing Intelligence**, and test both **Today** and **Tomorrow**.
The result must show the selected date and the number of verified master-case matches.

## Rollback

Restore the previous versions of `commands/ai.py` and `ai/knowledge_service.py`, and
remove `ai/prompts/hearing_intelligence.md`.
