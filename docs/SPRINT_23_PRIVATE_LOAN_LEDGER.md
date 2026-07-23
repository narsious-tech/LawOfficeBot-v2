# Sprint 23 — Private Loan Ledger & Interest Reminders

## Sprint 23.1 accounting refinement

The creation wizard now asks for the disbursement mode/reference and whether
the first month's advance interest was actually collected.

- If collected, an interest receipt is created automatically and the next due
  date becomes one month after the loan date.
- If not collected, the first interest remains due on the loan date.
- Users no longer type an arbitrary first due date.
- Currency is displayed using Indian digit grouping.
- **Opening Interest Correction** safely records the first installment for a
  legacy account whose due date was already advanced without a receipt. It
  does not advance that date a second time and refuses duplicate correction.

## Access

The loan ledger is separate from the staff financial ledger. It works only:

- in a private Telegram chat; and
- for the Telegram user configured in `ADMIN_USER_ID` (with existing admin
  variables retained as compatibility fallbacks).

No staff menu or office-group dashboard exposes loan information.

## Open the module

Run:

```text
/loanledger
```

The module provides:

- new-loan creation;
- active and historical accounts;
- reducing-balance monthly-interest calculation;
- separate interest and principal receipts;
- borrower, guarantor, security and maturity information;
- document register;
- recent transaction statement;
- overdue and upcoming-interest view;
- audit records and duplicate-safe automatic reminders.

## Interest policy implemented

- The entered rate is a monthly percentage.
- Interest is calculated on outstanding principal.
- Interest is payable monthly in advance.
- A principal receipt changes future monthly interest.
- Due interest must be recorded before a principal reduction.
- Interest receipts must equal one complete monthly installment or an exact
  multiple, preventing ambiguous partial-period balances.

Example:

```text
Outstanding principal: ₹5,00,000
Monthly rate: 1.5%
Monthly interest: ₹7,500
```

After a ₹1,00,000 principal receipt, future monthly interest is ₹6,000.

## Reminders

At 10:00 AM IST the bot privately alerts the administrator:

- three days before interest is due;
- on the due date; and
- daily while overdue.

Each daily alert is logged so a deployment retry does not duplicate it.

Use `/testloanreminders` to view the current due list without sending a
production reminder.

## Deployment

No manual SQL is required. The module creates its tables and indexes
idempotently during startup. The SQL migration is included for audit and
manual database administration.

Required existing Railway variable:

```text
ADMIN_USER_ID=<Ajay Telegram user ID>
```

## Verification

1. Confirm `/loanledger` opens only for Ajay in private chat.
2. Create one test loan with first advance interest collected and one without.
3. Verify the automatic receipt and calculated due dates.
4. Record one normal interest installment.
5. Confirm the next due date advances by one month.
6. Record a principal payment after interest is current.
7. Confirm outstanding principal and future interest reduce.
8. Add document names and confirm they appear in the account.
9. Run `/testloanreminders`.

## Rollback

Restore the previous `bot.py` and remove the loan command/service imports.
The new `private_loan_*` tables may remain safely dormant. Do not drop them
without first exporting or backing up the private ledger.
