# Sprint 49 — Owner-confirmed repair of older imported work

Apply after Sprint 48 and its Work Layout Hotfix. Extract changed files retaining folders and redeploy Railway.

The earlier Task #61 response has no case details or AD Work ID. The case parser is now working, but ID-only automatic repair cannot associate this older task. This update adds a private owner-only Telegram command to propose and confirm case context on those existing tasks.

Start with /repairworklinks 61. Select the case Kuldeep Singh VS Shivansh Mohan, CS/5365/2025, verify the preview and press Confirm repair. Staff Happy can then refresh /mytasks or WhatsApp MY WORK. Use /repairworklinks to review up to ten other pending imported tasks missing context. Re-run after confirmed repairs to reach more tasks. It does not send new task assignments or notifications automatically.

Candidates must have the exact normalized work description and complete case title/number. Existing case context and stored source Work ID must not conflict. Multiple candidates are presented to the owner rather than chosen automatically. The owner checks the correct case for each task. Confirm re-fetches the source and locks/rechecks the current task; changed records require a fresh preview. Previews expire after ten minutes or a restart, and consumed previews cannot be reused.

Only missing case_number, imported notes/title, and source_work_id are filled. The source ID is filled only when AD actually supplies it and it is not already assigned to another pending task. Missing IDs are not invented. Therefore /works may still show Not assigned where AD omits its Work ID; that label alone does not mean the repaired task has lost its staff assignment. Original task ID, assignee, status, deadlines, priority, description and completion state are retained. Repairs record actor and original/candidate context in ad_work_context_repairs. The audit table is created automatically in the confirmation transaction.

This command repairs existing local context; it does not fix missing completion links on the AD website. If no AD Work ID is supplied, this action cannot restore API completion linkage. Do not recreate or reassign the existing task to fix that.

Validation: 44 focused parser, work-context repair, owner access and staff/owner list tests passed; changed Python files compile. Actual production database repair, bot messaging and source fetch were not performed locally. The exact owner confirmation is required after deployment.
