# Sprint 16.1 — Evening Operations Dashboard Final

## Delivered
- Uses the official authenticated Advocate Diaries PDF endpoint:
  `/dashboard/download-day-cases-pdf?date=YYYY-MM-DD`
- `/printablecauselist` and `/printablecauselist tomorrow` now send the official PDF.
- The 4:30 PM Evening Dashboard attaches the official next-day PDF.
- Adds a court/floor/room grouped physical-file preparation list.
- Adds Telegram check-in buttons for:
  1. Files removed from cupboard
  2. Briefs/orders/documents checked
  3. Files placed in tomorrow's tray
- Adds `/filesready` to mark all three steps complete.

## Deployment
No new Railway variable is required. Existing Advocate Diaries credentials and either
`OFFICE_GROUP_CHAT_ID` or `PHYSICAL_FILE_GROUP_CHAT_ID` are required.

## Tests
1. `/printablecauselist tomorrow`
2. `/eveningdashboard`
3. Press each physical-file check-in button.
4. `/filesready`
