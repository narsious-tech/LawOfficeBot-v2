# Sprint 37 — WhatsApp Physical-File Selection

## Owner workflow

1. Send `EVENING` and tap **Select Files**, or send `FILES`.
2. WhatsApp shows seven next-court-day cases per page.
3. Tap a case to toggle it between unchecked and checked.
4. Use Previous/Next to review all hearings.
5. Tap **Review Selected**.
6. Check the final selected list and tap **Confirm Send**.

Additional owner commands:

- `FILES REVIEW` — review checked cases.
- `FILES CLEAR` — clear the next-court-day selection.
- `FILES AUTO` — apply the existing evidence/arguments/documents purpose rules.

## Delivery controls

- Only selected files are recorded in `physical_file_assignments`.
- Only Preet, Priya, Happy and Jimmy are delivery recipients.
- Each recipient receives the case number, title, court, floor, room and purpose.
- Delivery requires an inbound WhatsApp message from that staff number within 24 hours.
- Closed windows are reported to Ajay; no paid template is sent.
- Identical confirmed lists are protected against duplicate delivery.
- Draft selections are stored in PostgreSQL and survive a Railway restart.

## Deployment

1. Replace the files from this changed-files-only ZIP, preserving folders.
2. Redeploy Railway.
3. From the linked owner number, send `EVENING`.
4. Tap **Select Files**, select one test case and review it.
5. Before confirming, ask the intended staff recipients to send `HI` if their
   24-hour service window may have expired.

Tables are created idempotently at first use; no manual SQL migration is required.
