# Sprint 12.2 — Hearing Completion Workflow

From `/livehearings`, open a matter and tap **Complete Hearing**.

The guided workflow records:
- next date or disposal,
- next purpose/stage,
- order/outcome summary,
- documents or preparation required,
- optional follow-up task,
- optional client-update flag.

On confirmation it atomically:
- marks the live hearing Adjourned or Disposed,
- records a hearing completion and event history,
- updates the matched case next-hearing date,
- writes a client/case timeline entry,
- creates a pending task when selected.

Tables are created automatically. No manual SQL migration is required.
