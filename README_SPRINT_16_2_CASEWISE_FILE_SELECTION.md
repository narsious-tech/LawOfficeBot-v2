# Sprint 16.2 — Case-wise Physical File Selection

## What changed
- The generic three-step physical-file checklist is replaced with a checkbox/button for each case in tomorrow's cause list.
- The dashboard shows eight cases per page, with Previous/Next navigation.
- Tapping a case toggles it between selected and unselected.
- `Send selected files` sends one clean list of only the selected files to Preet, Priya, Happy and Jimmy.
- Staff Telegram IDs are resolved from `staff_accounts` by `staff_name`.
- The official Advocate Diaries PDF remains attached for reference.

## Test
1. Run `/eveningdashboard`.
2. Select several case-wise buttons.
3. Press `Send selected files`.
4. Confirm that Preet, Priya, Happy and Jimmy receive the same selected-file list.

## Requirement
Each recipient must have an active linked Telegram account in `staff_accounts` with the exact staff name: Preet, Priya, Happy or Jimmy.
