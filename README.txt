LAW OFFICE BOT — CASE-TITLE CHECKBOX UI PATCH

Changes:
1. /eveningdashboard
   - Detailed selector is title-first.
   - Clickable checkbox buttons use CASE TITLE instead of only case number.
   - Case number remains visible in the detailed matter.
   - Auto select / Select all / Send selected files / Clear selection stay unchanged.

2. /livehearings
   - Complete hearing details stay on the Live Hearing board.
   - Clickable open-hearing buttons use CASE TITLE instead of only case number.
   - Hearing status and completion workflow stay unchanged.

INSTALL
Place apply_case_title_ui_patch.py in the repository root (same folder as bot.py), then run:

python apply_case_title_ui_patch.py

It modifies only:
commands/evening_dashboard.py
commands/live_hearings.py

Commit/push those two changed files and allow Railway to redeploy.

TEST
1. /eveningdashboard -> buttons should show case titles.
2. Select files -> Send selected files.
3. /preparation -> selected matters should still appear.
4. /livehearings -> buttons should show case titles.
5. Open a hearing and change status.

No Railway variable changes are required.
