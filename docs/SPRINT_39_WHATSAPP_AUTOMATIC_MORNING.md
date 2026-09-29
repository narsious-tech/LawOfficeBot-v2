# Sprint 39 — Automatic WhatsApp Staff Morning Delivery

## Delivery

- At 10:05 AM Asia/Kolkata on each open morning, every active linked staff
  member receives a private WhatsApp morning bundle.
- The bundle contains the employee's own pending work, priorities, overdue and
  due-today counts, daily focus, a compact office hearing list, attendance state
  and the prompt for WhatsApp office controls.
- One employee never receives another employee's work list.
- The normal office calendar skips Sunday mornings and second/fourth-Saturday
  full-day holidays.
- A staff member who checks in after 10:05 receives a same-day catch-up if the
  scheduled delivery was missed.
- A database delivery ledger prevents duplicate automatic delivery.

## Cost protection

- Free-form delivery occurs only when the employee has messaged the office
  WhatsApp number within the preceding 24 hours.
- A closed window is recorded and skipped. The code never falls back to a paid
  template.
- WhatsApp check-in normally opens the service window. A late check-in can
  trigger the catch-up delivery.

## Railway variables

```text
WHATSAPP_AUTOMATIC_MORNING_ENABLED=true
WHATSAPP_MORNING_HOUR=10
WHATSAPP_MORNING_MINUTE=5
```

The hour and minute are interpreted in `Asia/Kolkata`. Existing WhatsApp Cloud
API and Staff Companion variables remain required.

## Verification

1. Deploy the changed files and keep automatic delivery disabled initially.
2. Ask one linked employee to send `HI` to open the 24-hour window.
3. In Ajay's private Telegram bot chat run:

   ```text
   /testwhatsappmorning Exact Staff Name
   ```

4. Confirm that the correct employee receives only their own work and the
   compact office hearing brief.
5. Set `WHATSAPP_AUTOMATIC_MORNING_ENABLED=true` and redeploy.

No Meta template is required by this sprint.
