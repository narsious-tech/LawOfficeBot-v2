# Sprint 34 — WhatsApp Staff Operations

This stage keeps the WhatsApp Staff Companion on the shared Office OS database
and adds controlled task completion to the existing hearings, work, attendance
status and case-search pilot.

## Staff menu

- Today Hearings
- Tomorrow Hearings
- My Work
- Office Status
- Attendance Status
- Case Search
- Help

## Work completion

The employee selects **My Work**, chooses a task assigned to their linked staff
identity, reviews the task and presses **Mark Completed**. The completion button
re-checks the assignment and current task status before changing anything.

Text alternative:

```text
DONE 17
```

This opens the same confirmation screen; it does not bypass confirmation.

When a task originated from Advocate Diaries, the bot completes the remote work
first. The Office OS task is changed only after Advocate Diaries returns success.
Failures leave the local task unchanged and direct the employee to retry or use
Telegram. Successful completions are recorded in the private staff activity feed.

## Still protected in Telegram

- Attendance check-in and check-out, until WhatsApp location verification is added
- eCourts approvals and conflict decisions
- Staff linking, unlinking and permissions
- Database diagnostics and repairs
- Sensitive administrative and financial commands

## Deployment and verification

Copy the changed files over the Railway project while preserving their folders,
then redeploy. No new Railway variable or manual SQL migration is required.

1. A linked employee sends `HI` to the office WhatsApp number.
2. Open **My Work** and select one assigned task.
3. Press **Cancel** and verify the task remains pending.
4. Repeat and press **Mark Completed** on a test task.
5. Confirm the task disappears from **My Work** and appears in Ajay's private
   staff-activity feed.

No scheduled outbound template or AI service is introduced by this sprint.
