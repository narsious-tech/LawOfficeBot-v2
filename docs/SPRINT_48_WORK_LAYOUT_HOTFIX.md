# Sprint 48 hotfix — Current Advocate Diaries work layout

Apply over Sprint 48, retaining folder paths, and redeploy Railway.

The supplied /work 1 response uses an unlabelled case title followed by a case-type/case-number line separated by a middle dot. The parser now handles this layout as well as the older labelled layout. The standalone pending/completed status is excluded from the hearing-date value.

Verified example: Kuldeep Singh VS Shivansh Mohan; Civil SUIT; CS/5365/2025; next hearing 22-10-2026; work Draft reply of POD.

After deployment run /works again in owner Telegram. This also invokes the Sprint 48 repair of blank imported-task case fields by exact AD Work ID. Then staff can request /mytasks in Telegram and MY WORK on WhatsApp. No task reassignment is needed. Already-populated task case fields are not replaced; missing source Work IDs cannot be repaired by guessing descriptions or client names. The imported assignment label must still be checked against existing links if it shows Not assigned.

Validation: 42 focused tests passed including the exact supplied layout and repair of its parsed case context. Parser compilation and ZIP integrity passed. Live source fetching, data repair and delivery were not exercised locally.
