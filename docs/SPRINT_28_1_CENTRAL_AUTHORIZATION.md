# Sprint 28.1 — Central Authorization

## Outcome

Every Telegram command and inline-button callback now passes through one access
gate before reaching legacy or modular handlers. Existing feature-level checks
remain active as defence in depth.

## Roles

- **Admin:** configured `ADMIN_USER_ID`, `AI_ADMIN_USER_IDS`, approved admin
  roles, and Ajay's linked profile.
- **Supervisor:** Priya or a linked supervisor/manager/senior profile.
- **Staff:** any other active Telegram-linked staff profile.
- **Unlinked:** bootstrap commands only.

## Deployment

Deploy `bot.py` and `commands/access_control.py`, restart Railway, then test:

1. Ajay: `/officestatus`, `/synccases`, `/ecourts`.
2. Priya: `/workcontrol`, `/assignwork`, `/pendingtasks`.
3. Staff: `/mytasks`, `/myworks`, `/checkin`.
4. Unlinked test account: `/office` should open; `/case` should be blocked.

## Important

`/linkstaff` remains available for onboarding in this sprint. Moving Advocate
Diaries passwords out of Telegram is the next security step.

`ADMIN_CHAT_ID` is deliberately not an authorization identity. It may identify
the office group, and granting authority through it would elevate every member.

`/linkstaff` is rejected in groups and may be used only in a private bot chat.
