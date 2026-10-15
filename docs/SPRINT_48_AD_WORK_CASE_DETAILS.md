# Sprint 48 — Case details in Advocate Diaries work lists

Extract the changed files over the existing project with Sprint 47 installed, retain folders and redeploy Railway. No new environment variables or database migrations are required.

The Advocate Diaries HTML parser now reads labels whose values appear in separate spans or lines, supports Case No. and case-insensitive labels, and does not swallow the next label when a field is blank.

Existing pending imported tasks are repaired by exact source_work_id, using the pending Advocate Diaries work list. Only blank task case_number and notes (the existing imported-case-title field) are filled. Existing notes, case numbers, task descriptions, assignments, deadlines, priorities, completion status and task IDs are retained. No guess is made from repeated work descriptions or staff names. Completed/deleted AD works absent from the pending source cannot be repaired by that fetch. A failed source fetch preserves the existing list and records an application log.

The repair runs before personal Telegram task lists, morning/evening staff briefs and WhatsApp work lists/details. Its source fetch is limited to once per 15 minutes per process; if no incomplete pending imported tasks exist it does not fetch. The owner /works command supplies freshly parsed records to refresh immediately regardless of cache. It can report how many existing tasks were repaired.

WhatsApp MY WORK shows case title and number separately, including AD title fallback where the local cases table lacks it. Picker descriptions and completion cards include imported case context. Staff and owner case joins exclude blank numbers and avoid choosing arbitrarily between conflicting titles for one case number. Unknown case context is labelled unavailable rather than attaching an unrelated case. Morning/evening briefs use repaired imported metadata through the existing shared builder.

After deployment: owner runs /works. Then Happy/other staff run /mytasks in Telegram and MY WORK in WhatsApp. Check tasks 60–63 against their authoritative AD Work IDs and verify the correct title and case number appear. No reassignment or task recreation is required. Existing messages are not edited; request fresh lists.

Verification: 39 focused parser, repair, staff/owner work, dashboard and morning-delivery tests passed. Changed Python files compile. Tests use source fixtures and mocked databases. Live AD data repair and message delivery were not performed locally.
