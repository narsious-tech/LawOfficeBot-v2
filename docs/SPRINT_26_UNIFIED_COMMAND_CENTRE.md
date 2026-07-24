# Sprint 26 — Unified Role-Aware Command Centre

Build: Sprint 26.0.1 registration compatibility hotfix.

## Entry points

- `/menu` — interactive Law Office OS control centre.
- `/command` — alias for `/menu`, preserving the previously discussed command function.
- `/commands` — authorised searchable command directory.
- `/help` — alias for `/commands`.

The earlier Sprint 19 `/start`, `/office`, `/mywork`, and `/supervisor`
interfaces remain available. No existing production command was removed.

## Role filtering

The menu detects:

- administrator IDs from `ADMIN_USER_ID`, `AI_ADMIN_USER_IDS`, and the
  compatibility value `ADMIN_CHAT_ID`;
- staff roles from `staff_accounts.role` when available;
- a schema-safe staff fallback when the role column is absent.

Private loans, eCourts administration, WhatsApp administration, synchronization,
and Ajay AI are hidden from ordinary staff. Feature handlers still perform their
own authorization checks when opened.

## Command registry

`commands/command_centre.py` is the single curated registry for button labels,
descriptions, categories, minimum roles, usage text, and safe direct launch.
Future features should be added to `ITEMS` there.

Commands that require arguments or start a conversation show exact usage rather
than bypassing Telegram conversation-state tracking.

## Deployment

Replace/add:

- `bot.py`
- `commands/command_centre.py`
- `docs/SPRINT_26_UNIFIED_COMMAND_CENTRE.md`

No database migration or new Railway variable is required.

## Verification

1. Run `/menu` as administrator.
2. Confirm Accounts includes the private loan ledger and Court Operations
   includes eCourts.
3. Run `/menu` as ordinary staff and confirm those restricted actions are hidden.
4. Run `/commands attendance`.
5. Run `/command` and confirm it opens the same interactive menu.
6. Confirm `/office` still opens the earlier Office OS screen.
