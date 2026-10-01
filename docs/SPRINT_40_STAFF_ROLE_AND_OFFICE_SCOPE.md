# Sprint 40 — Staff Role and Attendance Office Scope

## Added controls

- Multi-word staff names are supported through the pipe-separated `/linkstaff`
  format.
- The administrator can assign a staff role and attendance-office scope using
  `/setstaffprofile`.
- Supported roles: `staff`, `junior_associate`, `clerk`, `supervisor`, `manager`.
- Supported scopes: `all`, `court_only`, `evening_only`.
- Office restrictions are enforced in both Telegram Web App attendance and
  WhatsApp location attendance.
- WhatsApp activity records carry the employee's actual role.
- Every role/scope change is retained in `staff_profile_audit`.

## Isha onboarding

1. Isha opens the Telegram bot privately and sends:

   ```text
   /linkstaff Isha Dua | HER_AD_EMAIL | HER_AD_PASSWORD
   ```

   Credentials must be entered only in her private bot chat and must never be
   shared in a group or support conversation.

2. Ajay runs in his private Telegram bot chat:

   ```text
   /setstaffprofile Isha Dua | junior_associate | court_only
   /linkwhatsapp 918146970807 Isha Dua
   ```

3. Isha sends `HI` to the office WhatsApp number.

4. Verify with `/linkedstaff` and `/whatsappstaff`.

The `court_only` scope permits attendance only at `Court Chamber Office` and
blocks `Evening Office` attendance while preserving hearings, assigned work,
case search, morning brief and non-administrative staff controls.
