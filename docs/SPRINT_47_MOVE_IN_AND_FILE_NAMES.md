# Sprint 47 — WhatsApp office transfers and file names

Apply over the project with Sprint 46 installed, keeping folder paths, then redeploy Railway. Only four application files are changed. No Railway variables or paid templates are required.

WhatsApp staff menu now includes Move In / Transfer. Office Status remains available by typing OFFICE STATUS. The menu stays within the existing ten-row limit. MOVE IN, MOVEIN, /movein, TRANSFER, OFFICE TRANSFER and MOVE OFFICE route to a transfer request; /checkin and /checkout are also recognised.

A transfer requires an open attendance session. Staff must arrive at the destination, send their current location and confirm. The destination must be an active office within the staff member’s permitted office scope/radius. Transfers to the current office are rejected. Successful transfer adds a movement/location record, updates the current office and sends the existing office-group attendance notification including source and destination. It does not punch Advocate Diaries again, reset check-in time, close attendance or overwrite working duration. Check Out remains the final departure action. Move In records arrival, not departure/transit.

The existing pending-action check constraint is extended automatically to allow MOVE when the attendance flow first runs; no separate manual SQL is required. Original attendance tables and credentials remain as before. First use after deploying should be checked for database constraint errors if the deployed schema differs from the standard project.

Telegram file selector buttons now display case title and number, with full titles still in the message body. WhatsApp picker titles show the party name, and descriptions retain the case number and title. Selection callbacks, pagination, saved assignments and all-staff delivery remain unchanged. Long interactive labels are abbreviated; no case title data is altered. Open a fresh selector after deploying; old message buttons do not redraw until refreshed.

Test: send MENU from a staff WhatsApp, choose Move In / Transfer after an existing check-in, share current location at the other approved office and confirm. Check ATTENDANCE STATUS for current office and verify the transfer alert. Then open a fresh evening file selection page in Telegram and FILES from owner WhatsApp to see party names.

Validation: 27 focused tests passed, including missing/closed-session rejection, same-office rollback, transfer audit and open-session preservation without AD punch, aliases/menu limit, and Telegram/WhatsApp picker names. All changed application files compile. Tests use mocks; no live attendance, database migration or external messages were performed locally.
