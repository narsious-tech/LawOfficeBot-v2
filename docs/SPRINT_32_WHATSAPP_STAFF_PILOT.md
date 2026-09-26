# Sprint 32 — WhatsApp Staff Bot Pilot

This phase makes WhatsApp the practical staff entry point while Telegram stays
available to Ajay as the private administrator and emergency console.

## Staff functions

- Full WhatsApp list menu
- Today hearings
- Tomorrow hearings
- My Work
- Office Status
- Attendance Status
- Case search by number or title
- Help
- Private staff-activity notification to Ajay

Telegram and WhatsApp now share the same resilient Advocate Diaries hearing
service. An expired access token is renewed automatically once, and the old
60-page `/todayhearings` and `/tomorrowcause` scan is removed.

## Protected during the pilot

Task completion, attendance check-in/out, eCourts approvals, staff linking and
database administration remain protected in Telegram until the staff identity
pilot is verified. WhatsApp currently exposes attendance status only.

## Railway settings

```
WHATSAPP_ENABLED=true
WHATSAPP_STAFF_COMPANION_ENABLED=true
WHATSAPP_PHONE_NUMBER_ID=<production phone number ID>
WHATSAPP_ACCESS_TOKEN=<permanent system-user token>
WHATSAPP_VERIFY_TOKEN=<private webhook token>
WHATSAPP_APP_SECRET=<Meta app secret>
WHATSAPP_GRAPH_VERSION=v26.0
```

Link each pilot staff member privately in Telegram:

```
/linkwhatsapp 919876543210 Exact Staff Name
```

The linked employee sends `HI` to the office WhatsApp number and selects an
item from **Office Menu**.
