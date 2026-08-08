CASE FILE CONTROL + HEARING PREPARATION V2
==========================================

This pack is designed around the CURRENT LawOfficeBot-v2 physical-file system.

NEW FILES
---------
services/hearing_preparation_v2_service.py
commands/hearing_preparation_v2.py

BOT.PY CHANGES
--------------
1) Add import near the existing hearing/readiness imports:

from commands.hearing_preparation_v2 import (
    preparation,
    preparationstatus,
    hearing_preparation_callback,
)

2) Add these handlers near /readiness and /myfilesstatus:

app.add_handler(CommandHandler("preparation", preparation))
app.add_handler(CommandHandler("preparationstatus", preparationstatus))
app.add_handler(CallbackQueryHandler(hearing_preparation_callback, pattern=r"^hp2:"))

HOW IT WORKS
------------
Existing /eveningdashboard remains the file-selection authority.
It already auto-selects evidence/cross/arguments/consd/consideration/documents/
record/reply/replication matters.

After "Send selected files", the existing physical_file_assignments records
become the source for V2 preparation.

Run:
/preparation

Each selected matter gets button controls for:
- Physical file brought / missing / attention
- Documents checked / attention
- Previous order checked / not required
- Client/instructions ready / not required

A matter becomes READY only when all four preparation gates are satisfied.

Use:
/preparationstatus

for the concise management summary.

IMPORTANT
---------
This deliberately does NOT replace /eveningdashboard, /readiness,
 /morningreadiness, or /myfilesstatus. It adds a preparation-control layer
on top, so current staff workflow is not broken.

DATABASE
--------
The hearing_preparation table is created automatically on first use.

DEPLOYMENT
----------
Upload the two new files, make the three small bot.py registrations above,
commit, and let Railway redeploy.
