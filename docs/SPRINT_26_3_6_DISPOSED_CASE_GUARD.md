# Sprint 26.3.6 — Disposed Case Guard

Disposed and otherwise terminal Advocate Diaries cases are excluded from the
eCourts date-verification workflow.

Terminal statuses:

- `DISPOSED`
- `CLOSED`
- `DECIDED`
- `ARCHIVED`
- `INACTIVE`

The guard applies at four points:

1. eCourts date reconciliation does not create new conflicts for terminal cases.
2. Existing pending conflicts are changed to `DISPOSED_SKIPPED` on the next sync.
3. Advocate Diaries hearing-date write-back is blocked for terminal cases.
4. Failed or queued write-backs for terminal cases are not retried.

Deploy the changed service files, then run `/syncecourts` followed by
`/ecourtsdatecheck`. Disposed cases must not appear in the decision queue.
