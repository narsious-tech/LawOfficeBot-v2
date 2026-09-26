# WhatsApp Owner Desk

The owner desk uses the office's existing WhatsApp Cloud API number and PostgreSQL database. It provides private, read-only office information to the one phone number linked through the administrator's private Telegram chat.

## Deploy

Copy the changed files from this package over the project, keeping directory paths. Deploy the same Railway service. No new Railway variables or migrations are required. `ADMIN_USER_ID`, `WHATSAPP_ENABLED=true`, and `WHATSAPP_STAFF_COMPANION_ENABLED=true` must already be configured.

In Ajay's private Telegram chat, run:

```text
/linkwhatsappowner 919815908700
/whatsappowner
```

Use Ajay's personal WhatsApp number in the first command. The office Cloud API sender number is different. Send `HI` from the linked personal number to the office WhatsApp number (`+91 70095 93717`). A separate Owner Desk menu will appear.

## Owner commands

- `OVERVIEW`: total pending and overdue tasks, grouped by assignee.
- `ACTIVITY`: six recent staff actions from the office activity feed.
- `WORK`: first ten pending tasks across the office, with overdue tasks first.
- `CASE <number or title>`: read-only case search.
- `HI` or `MENU`: return to the owner menu.

Only Telegram's configured `ADMIN_USER_ID` can link, replace, or unlink the owner phone. The bot compares the normalized WhatsApp sender to that link on every message. It does not grant owner access to other staff numbers. The owner menu does not change tasks, cases, attendance or approvals. Staff activity alerts continue in the owner's private Telegram chat; `ACTIVITY` provides an on-demand WhatsApp view without outbound template messages.

To revoke the owner phone, run `/unlinkwhatsappowner` in the same private Telegram chat. If the phone is lost, unlink it promptly. A new `/linkwhatsappowner` command replaces the previous owner number.

## Verification

1. Confirm `/whatsappowner` shows only the personal owner number.
2. Send `HI` from that number: the Owner Desk menu should appear.
3. Send `OVERVIEW`, `ACTIVITY`, `WORK`, and `CASE CS/2112/2022`.
4. Send `HI` from a linked staff number: that number should still see only the staff menu.
