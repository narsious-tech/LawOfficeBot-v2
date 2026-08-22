# Sprint 29 — WhatsApp Staff Companion

This release adds a low-risk, read-only WhatsApp surface over the existing
Office OS database. Telegram remains the secure Command Centre for attendance,
updates, approvals, completion controls and administration.

## Staff features

Linked staff can message the office WhatsApp number and use:

- `HI`, `MENU` or `HELP` — menu with reply buttons
- `MY WORK` — their pending assigned work
- `OFFICE STATUS` — their pending/overdue work and attendance state
- `CASE <number or title>` — read-only Office OS case search

Check-in and check-out stay in Telegram because attendance requires verified
location. Every linked staff WhatsApp message is written to the staff activity
audit and privately reported to Ajay through the existing activity destination.

## Railway variables

Set these in the bot service:

```text
WHATSAPP_ENABLED=true
WHATSAPP_STAFF_COMPANION_ENABLED=true
WHATSAPP_PHONE_NUMBER_ID=<Meta phone number ID>
WHATSAPP_ACCESS_TOKEN=<permanent system-user token>
WHATSAPP_VERIFY_TOKEN=<a private value you choose>
WHATSAPP_APP_SECRET=<Meta app secret>
WHATSAPP_GRAPH_VERSION=v23.0
```

Keep `ADMIN_USER_ID` configured so staff activity goes only to Ajay.

Configure the Meta webhook as:

```text
https://<railway-public-domain>/whatsapp/webhook
```

Subscribe the WhatsApp Business Account to the `messages` webhook field. The
verify token entered in Meta must exactly match `WHATSAPP_VERIFY_TOKEN`.

## Link staff identities

In Ajay's private Telegram chat:

```text
/linkwhatsapp 919876543210 Exact Staff Name
/whatsappstaff
/unlinkwhatsapp 919876543210
/whatsappstatus
```

Use international digits without `+`. A number can be linked to only one active
staff account. All link and unlink actions are audited.

## Deployment sequence

1. Deploy the changed files.
2. Add the Railway variables, initially leaving
   `WHATSAPP_STAFF_COMPANION_ENABLED=false`.
3. Configure and verify the Meta webhook.
4. Run `/whatsappstatus` and `/testwhatsapp <your-number>`.
5. Link one pilot staff number and set the companion flag to `true`.
6. Ask the pilot user to send `HI`, then test each menu action.

Disable only `WHATSAPP_STAFF_COMPANION_ENABLED` for an immediate rollback;
Telegram and existing client WhatsApp transport continue unchanged.
