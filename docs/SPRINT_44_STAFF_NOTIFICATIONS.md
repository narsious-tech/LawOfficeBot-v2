# Sprint 44 — Staff notifications

Extract the changed files over the existing project, preserving folders, then redeploy Railway. No new variables or paid Meta templates are required.

Priya and Preet receive personal Telegram sync results for scheduled, command and dashboard eCourts backup syncs. They also receive the consolidated pending case changes, with titles, dates and CNRs, on Telegram and WhatsApp. Existing administrator delivery and approval permissions remain unchanged. Staff records are matched by exact names Priya and Preet; both must have active linked accounts.

The owner-selected files-required list is delivered to every active staff WhatsApp account, including associates. This applies to selections confirmed in either Telegram or WhatsApp. Telegram file-list recipients remain unchanged. This sends the selected physical-file list, not all unselected hearing files.

WhatsApp messages use the free 24-hour inbound-message window. Outside it, lists and alerts are queued and delivered when linked staff next message the bot. Files expire after the target court date; eCourts messages expire after three days. There is no paid-template fallback. Queue entries and chunk progress persist in PostgreSQL, and duplicate event keys are ignored. API submission is not proof of delivery. A database failure after provider acceptance can still leave ambiguous delivery; watch provider status for such incidents.

Test: run /syncecourts and check Priya and Preet personal Telegram. Have them send HI on WhatsApp to receive queued messages. Select files and confirm send; check the result counts and have all staff send HI if queued. Missing WhatsApp links must be configured through the existing link commands. Ensure WHATSAPP_STAFF_COMPANION_ENABLED=true for catch-up routing.

Validation: 14 focused tests passed; all five changed application files compile. No live database or external messages were used during local verification.
