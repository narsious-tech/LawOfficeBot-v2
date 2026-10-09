# Sprint 46 — Priya date management

Deploy after Sprints 44 and 45. Extract changed files retaining folders and redeploy Railway. No general admin role change is needed. The default delegate is Priya’s previously linked Telegram account 8413754577. Active linked staff membership is checked on every action. If her linked ID has changed, set ECOURTS_DATE_MANAGER_TELEGRAM_ID to her verified current Telegram ID; an empty/invalid value disables the delegation.

Telegram: Priya privately sends /ecourtsdatecheck (alias /ecourtsdates). She can Accept eCourts Date, Keep Staff Date or Review Later. Broader eCourts administration, imports, case linking, AI approvals and other owner actions remain denied. The copied sync summary also tells Priya how to open the date desk.

WhatsApp: Priya sends DATES or ECOURTS DATES from her linked number. The desk refreshes live Advocate Diaries comparisons, then presents a pending conflict with case title, CNR and both dates. Choosing Accept, Keep or Later displays a confirmation. Confirmations belong to the actor, expire after ten minutes, and are consumed once. Changed date snapshots require a fresh preview; Cancel invalidates her outstanding confirmations. Requires WHATSAPP_STAFF_COMPANION_ENABLED=true. No paid templates are used for these inbound replies.

Accept uses the existing audit and Advocate Diaries writeback path, including its verification and retries. Actor ID records Priya as decision-maker. Owner receives a WhatsApp activity alert through the existing Telegram feed. A successful Office OS decision with AD pending/failure is not presented as verified AD success. No custom arbitrary date editing was added; these controls review the staff/eCourts discrepancy.

Next-day deferral and stale-date rejection remain in force. General administrator controls retain existing access. Other staff, including Preet, retain read-only sync notifications.

Validation: 22 focused tests passed for permissions, identity/activity checks, expired/reused confirmation rejection, forged control denial and date comparison rules. Changed Python files compile. Live messages/database were not used locally. Test first in Priya’s personal Telegram using /ecourtsdatecheck and WhatsApp using DATES; if no conflicts exist the desk reports that state without changing anything.
