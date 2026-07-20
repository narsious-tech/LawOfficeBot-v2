# Sprint 16.0.2 — Dedicated Pending Cases Parser

## Fix
- Uses the authenticated server-rendered `GET /pendingCases` page only.
- Parses only `table#cases-list > tbody > tr[id^="case_"]`.
- Removes the obsolete generic `/court_cases` API filtering from `/pendingcases`.
- The 5:00 PM report, case-owner routing, Priya supervision report and `/pendingcases` now share one source.
- An empty pending table means zero pending updates; it never falls back to the full case inventory.

## Verification
Run `/pendingcases`. For the supplied Advocate Diaries page it should report exactly 8 records. Then run `/nextdateslist` to verify the combined 5:00 PM preview.
