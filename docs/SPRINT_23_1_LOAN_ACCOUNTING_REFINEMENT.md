# Sprint 23.1 — Private Loan Accounting Refinement

## Delivered

- Indian currency grouping (`₹10,00,000.00`).
- Disbursement mode and reference captured during loan creation.
- First advance-interest collection explicitly recorded.
- Automatic first-interest transaction when collected at disbursement.
- Automatic next-interest date:
  - collected: one month after the loan date;
  - not collected: due on the loan date.
- One-time Opening Interest Correction for Sprint 23 legacy accounts.
- Duplicate and amount validation for the correction.

## Correcting LN-2026-00001

Open `/loanledger`, select `LN-2026-00001`, then:

1. Tap **Opening Interest Correction**.
2. Enter `30000`.
3. Select the actual receipt mode.
4. Enter the reference or `skip`.

The account keeps its next due date as `2026-08-23`; it must not advance to
September. Use the normal **Interest Receipt** button for future installments.

## Deployment

Replace:

- `commands/loan_ledger.py`
- `services/loan_ledger_service.py`

No new Railway variables or manual database migration are required.

## Verification

1. Run `/loanledger`.
2. Confirm Indian currency formatting.
3. Create a small loan with first interest marked `yes`.
4. Confirm the first interest transaction exists and next due is one month later.
5. Create a small loan with first interest marked `no`.
6. Confirm the next due date equals the loan date.
7. Test Opening Interest Correction once and confirm a second attempt is rejected.
