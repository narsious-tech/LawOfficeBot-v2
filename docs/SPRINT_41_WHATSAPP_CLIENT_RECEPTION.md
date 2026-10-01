# Sprint 41 — WhatsApp Client Reception Desk

## Purpose

Unknown WhatsApp numbers are handled as clients or new enquiries after owner and
linked-staff routing. Telegram remains the private administration and audit channel.

## Client menu

1. Existing Client
2. New Legal Enquiry
3. Book Appointment
4. Case / Hearing Info
5. Documents Required
6. Location & Timings
7. Contact the Office

## Privacy and cost safeguards

- A sender can see a case summary only when that exact WhatsApp number is stored
  in `cases.mobile` or `client_contacts.whatsapp_number` for that case.
- Every selected case is re-authorized against the sender phone before disclosure.
- Unknown numbers may submit identification details, but receive no case data.
- The bot never gives automated legal advice.
- Replies are sent only after an inbound client message, inside Meta's service window.
- No paid template fallback is used by the reception desk.
- Every interaction is copied to Ajay's private Telegram chat (`ADMIN_USER_ID`).
- Owner and linked-staff WhatsApp routing remains ahead of client routing.

## Railway variables

Deploy first with:

```text
WHATSAPP_CLIENT_RECEPTION_ENABLED=false
```

Confirm the existing office-profile values:

```text
OFFICE_NAME=Law Office of Ajay Chawla
OFFICE_PHONE_NUMBER=
OFFICE_EMAIL=
OFFICE_HOURS=Monday-Saturday, 9:30 AM-6:30 PM
COURT_OFFICE_ADDRESS=Chamber No. 247, District Courts, Ludhiana
COURT_OFFICE_MAPS_LINK=
EVENING_OFFICE_ADDRESS=
EVENING_OFFICE_MAPS_LINK=
```

After deployment, run `/whatsappclientstatus` in Ajay's private Telegram chat.
Then enable:

```text
WHATSAPP_CLIENT_RECEPTION_ENABLED=true
```

Test from an unlinked personal number by sending `HI`. Never test from the linked
owner or a linked staff number because those numbers intentionally receive their
own menus.

## Data

- `whatsapp_client_reception_state`: short, two-hour multi-step conversation state.
- `whatsapp_client_requests`: reviewable enquiry, appointment, document and general
  message requests.
- Existing `whatsapp_inbound_messages`: complete inbound audit trail and `/whatsappinbox`.
