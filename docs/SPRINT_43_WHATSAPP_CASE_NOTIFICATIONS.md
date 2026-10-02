# Sprint 43 — WhatsApp D-2 Hearing and Material Case Notifications

## What this release does

- Sends one hearing reminder exactly two calendar days before the current next-hearing date.
- Sends a correction when Advocate Diaries changes the hearing date.
- Sends material status updates only for disposal/closure/cancellation-type changes.
- Sends action-required updates only when the purpose newly asks for personal presence or specified documents/filing.
- Stops D-2 reminders for closed/disposed matters.
- Blocks duplicate events with a unique notification ledger.
- Counts successful Meta submissions against a conservative monthly cost estimate.
- Stops automatic submissions at the configured monthly cap and alerts Ajay privately.
- Requires explicit phone-level opt-in. A client can enable or stop reminders from the WhatsApp reception menu.

## Meta templates to create

Create both as **Utility** templates in WhatsApp Manager. Use one body variable in each.

### `case_hearing_reminder`

```text
Hearing reminder from Law Office of Ajay Chawla:
{{1}}
Please contact the office if clarification is required.
```

### `case_status_update`

```text
Case update from Law Office of Ajay Chawla:
{{1}}
This is an office record update, not legal advice.
```

Do not enable automatic delivery until Meta shows both templates as **Approved**.

## Railway variables

Deploy first with all three feature flags disabled:

```text
WHATSAPP_CASE_REMINDERS_ENABLED=false
WHATSAPP_CASE_STATUS_UPDATES_ENABLED=false
ADVOCATE_DIARIES_STATUS_POLL_ENABLED=false
WHATSAPP_CASE_REMINDER_DAYS=2
WHATSAPP_CASE_REMINDER_TEMPLATE=case_hearing_reminder
WHATSAPP_CASE_STATUS_TEMPLATE=case_status_update
WHATSAPP_TEMPLATE_LANGUAGE=en
WHATSAPP_CASE_NOTIFICATIONS_MONTHLY_CAP_INR=150
WHATSAPP_UTILITY_RATE_INR=0.115
ADVOCATE_DIARIES_STATUS_POLL_MINUTES=60
```

After deployment, run in Ajay's private Telegram chat:

```text
/whatsappreminderstatus
/testwhatsappreminders
```

The dry run never sends a WhatsApp message. It also creates the first case snapshot, preventing old changes from being sent as if they were new.

## Consent

Preferred client method:

1. Client sends `MENU` to the office WhatsApp number.
2. Client chooses **Enable Reminders**.
3. The number must already match a registered case.
4. Client may choose **Stop Reminders** or send `STOP REMINDERS` at any time.

Ajay may record consent already obtained outside WhatsApp:

```text
/whatsappconsent CASE_NUMBER on
/whatsappconsent CASE_NUMBER off
```

Use `on` only when the client's express consent has been recorded.

## Safe activation order

1. Deploy with all notification flags disabled.
2. Run `/testwhatsappreminders` once to establish the baseline.
3. Confirm both Meta templates are approved.
4. Have one internal/pilot registered number opt in.
5. Set `WHATSAPP_CASE_REMINDERS_ENABLED=true`.
6. Run `/testwhatsappreminders` again and wait for a genuine D-2 case.
7. Set `WHATSAPP_CASE_STATUS_UPDATES_ENABLED=true`.
8. Finally set `ADVOCATE_DIARIES_STATUS_POLL_ENABLED=true` for hourly office-hours checks.

The lightweight poll runs only between 08:00 and 20:00 IST. The full 08:45 Advocate Diaries sync also triggers the notification scan.

## Cost guard

The cap uses successful Meta API submissions, not retries or dry runs. The default estimate is ₹0.115 per Utility template before taxes and the default monthly cap is ₹150. Update `WHATSAPP_UTILITY_RATE_INR` whenever Meta changes the applicable India rate.

The cap is deliberately conservative. It prevents new automatic submissions; it does not affect inbound WhatsApp reception or staff commands.
