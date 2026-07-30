# Sprint 27 — e-Jagriti Consumer Bridge

This release adds an administrator-only workflow for consumer commission
matters that are checked on e-Jagriti.

## Safety model

- CAPTCHA and official website inspection remain manual.
- Consumer matters are excluded from the ordinary eCourts date-verification
  queue.
- Office OS dates change only after an administrator accepts the comparison.
- Advocate Diaries is not changed automatically because its consumer-case
  update endpoint has not been verified.
- Original e-Jagriti data is recorded as an audit snapshot.

## Commands

```text
/ejagriti
/ejagritilink CASE | FILING_REF | FULL_CASE_NO | COMMISSION
/ejagritiupdate CASE | LAST_DATE | NEXT_DATE | PURPOSE | STAGE | HISTORY_COUNT
/ejagritireview
```

To store an order, reply to its PDF with:

```text
/ejagritiorder CASE | ORDER_DATE
```

Dates may use `DD-MM-YYYY`, `DD/MM/YYYY`, or `YYYY-MM-DD`.

## First test

For `CC/23/351`, obtain the filing reference and full e-Jagriti case number
from the official case-details page, then run:

```text
/ejagritilink CC/23/351 | FILING_REFERENCE | FULL_CASE_NUMBER | DCDRC Ludhiana
/ejagritiupdate CC/23/351 | LAST_DATE | NEXT_DATE | PURPOSE | STAGE | HISTORY_COUNT
/ejagritireview
```

## Deployment

Deploy the complete project or copy all files from the changed-files package.
The tables are created automatically when the desk is first opened. The SQL
migration is included for controlled/manual migration workflows.
