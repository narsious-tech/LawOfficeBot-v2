# Sprint 26.2 — Personal Motivation & Accountability

## Delivered behaviour

- The office morning dashboard contains one original daily morning thought.
- The evening operations dashboard contains one original evening reflection.
- Every linked active staff member receives a private motivational section in the
  existing 9:10 AM work brief.
- Every linked active staff member receives a private day-closing board at
  5:30 PM IST.
- Saturday evening delivery is suppressed because the evening office is closed.
- Sunday delivery remains active within the Sunday 2:00–6:00 PM office window.

## Accountability rules

- Messages rely only on recorded pending tasks and deadlines.
- Overdue messages name the relevant task IDs.
- Light wit is used only in the staff member's private bot chat.
- Group and administrator reports remain factual.
- Personal traits, appearance, intelligence, family, religion, caste, gender,
  health and other personal characteristics are never used.
- When the available task text records approved leave, a blocker, an unavailable
  system or another external dependency, the critical line is suppressed and
  replaced with a neutral request to record the next follow-up step.

## Manual test

Run:

`/teststaffbriefs`

This sends the current private morning briefs.

Run:

`/teststaffclosing`

This forces an immediate test delivery of the current private evening
accountability boards, including on Saturday. The automatic 5:30 PM job still
honours the office calendar and skips every Saturday evening.

## Rollback

Remove the 5:30 PM scheduler registration and the `/teststaffclosing` handler
from `bot.py`, restore `commands/dashboard.py` and
`commands/evening_dashboard.py`, and remove
`services/staff_motivation_service.py`.
