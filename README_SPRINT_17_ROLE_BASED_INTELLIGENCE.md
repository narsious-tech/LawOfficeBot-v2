# Sprint 17 — Role-Based Intelligence Layer

## Commands
- `/mydashboard` — personal, role-filtered operational dashboard.
- `/officestatus` — office-wide dashboard restricted to Ajay and Priya (or admin/owner/supervisor roles).
- `/myfilesstatus` — tomorrow's selected physical files with case-level status buttons.

## Physical-file workflow
1. Ajay selects required matters in `/eveningdashboard`.
2. The selection is persisted in `physical_file_assignments`.
3. Preet, Priya, Happy and Jimmy receive the same selected list plus a status card for each file.
4. Each card supports: `Brought`, `Not found`, and `Needs attention`.
5. `Not found` and `Needs attention` automatically escalate to Ajay and Priya.

## Database
The bot creates `physical_file_assignments` automatically on first use. No manual migration is required.

## Test
1. Run `/eveningdashboard`, select cases and press `Send selected files`.
2. Confirm all four recipients receive case cards.
3. Mark one file `Brought` and one `Not found`.
4. Confirm Ajay and Priya receive the exception alert.
5. Run `/mydashboard`, `/officestatus`, and `/myfilesstatus`.
