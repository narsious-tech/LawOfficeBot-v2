# Sprint 35 — WhatsApp Attendance

Linked staff can now check in and check out from the WhatsApp Staff Companion.
Telegram remains the administrator console for corrections and approvals.

## Staff flow

1. Send `HI` and select **Check In** or **Check Out**.
2. Use WhatsApp's attachment menu and send the current location.
3. Review the detected office and distance.
4. Press **Confirm** or **Cancel**.
5. The bot completes Advocate Diaries first and then updates the existing Office
   OS attendance session and location records.

Staff may also send `CHECK IN`, `CHECK OUT`, `ATTENDANCE STATUS`, or `CANCEL`.

## Safeguards

- Only a linked active staff WhatsApp number can start an action.
- A location is accepted only for a pending action and within the configured time.
- Forwarded locations are rejected when Meta flags them as forwarded.
- The coordinates must be inside an active office's configured radius.
- Duplicate actions inside `ATTENDANCE_DUPLICATE_MINUTES` are rejected.
- Check-in is blocked while an open session exists.
- Check-out requires today's open session.
- The assignment is rechecked before confirmation.
- Advocate Diaries failure leaves the Office OS session unchanged.
- Confirm/Cancel is mandatory before any punch.
- Every successful action enters Ajay's private staff-activity feed.

WhatsApp Cloud API location messages contain latitude and longitude but do not
contain GPS accuracy metres. Therefore the Telegram Web App's accuracy-metre
check cannot be reproduced on WhatsApp. This implementation uses a fresh
message timestamp, forwarded-message rejection when available, approved-office
radius verification and explicit confirmation.

## Owner Desk

Send `ATTENDANCE` or `TODAY ATTENDANCE` to see all linked active staff as
present, checked out, or not checked in.

## Settings

No new mandatory Railway variable is required. Optional:

```text
WHATSAPP_ATTENDANCE_PENDING_MINUTES=10
ATTENDANCE_DUPLICATE_MINUTES=10
```

The existing `attendance_offices` table remains authoritative for coordinates,
radius and whether each office permits check-in or check-out.
