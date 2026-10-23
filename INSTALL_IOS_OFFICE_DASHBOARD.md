# iPhone Office Dashboard installation

Prepared against narsious-tech/LawOfficeBot-v2 commit
`5b02e925d288def7a24bfa4ad9e4a7a3acf1a318` (GitHub connected, read-only inspection).
No GitHub or production changes were made. The earlier
`/mnt/data/ios_office_dashboard_patch.zip` was not present. The referenced chat
could not be fetched because no compatible read_thread tool was available.

## Upload

1. Back up the two existing files. Compare your branch with the pinned commit
   before replacing them; if it has moved, merge the small changes rather than
   overwriting newer work.
2. Extract this ZIP. Upload these paths into the repository root, preserving paths:
   - `attendance_app.py` (adds endpoint registration only)
   - `services/role_intelligence_service.py` (adds optional read-only mode)
   - `services/ios_office_dashboard.py` (new endpoint)
   - `tests/test_ios_office_dashboard.py` (new isolated regression tests)
   - this INSTALL file and `VERIFICATION.md` (new documentation)
3. No requirements, Procfile, bot.py, templates, or database migrations change.
   Existing `bot.py` already starts `run_attendance_app` in a thread, and the
   Flask app already listens on `0.0.0.0` using Railway's `PORT` (fallback 8080).
   Keep the existing start command; do not start a second Telegram polling bot.
4. Test in a staging environment with existing initialized schema first. The
   endpoint deliberately cannot initialize missing tables. Use the existing
   bot's normal initialization workflow; do not grant this endpoint DDL access.
5. Commit/upload manually and deploy only when ready. On Railway, use the public
   HTTPS domain of the service running this existing Flask app. Under Settings >
   Networking > Public Networking, generate a domain if needed. Match its target
   port to the app's PORT. Do not change production on the basis of this ZIP alone.

## Exact Railway variables

Set these on the same service/environment as the existing bot and Flask app:

| Variable | Value |
| --- | --- |
| `IOS_OFFICE_DASHBOARD_ENABLED` | `true` to enable; `false` to disable immediately after redeploy |
| `IOS_OFFICE_DASHBOARD_TOKEN` | A newly generated URL-safe random token; generate with the command below |
| `ADMIN_USER_ID` | Existing owner's positive numeric Telegram **user** ID; keep its existing correct value |

Generate a secret locally: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Copy the output into Railway and your private Shortcut. Never commit it. This
produces 43 URL-safe characters; endpoint accepts 43–128 letters, numbers, `_`, `-`.
Use a fresh randomly generated value, never a repeated-character example.

Keep existing `DATABASE_URL` (required PostgreSQL connection), `AD_EMAIL`,
`AD_PASSWORD`, and the rest of your bot/Advocate Diaries settings unchanged.
They are existing integration settings, not new dashboard secrets. No new
`AD_API`, Telegram token, OpenAI key, iOS version flag, or public URL variable
is required by this patch. Railway supplies `PORT`; do not set a new value
unless matching your existing networking configuration. `ADMIN_CHAT_ID` cannot
substitute for `ADMIN_USER_ID` here.

## iPhone Shortcut (manual creation)

1. Open Shortcuts > +. Name it **Office Dashboard**.
2. Add **URL**: `https://YOUR-SERVICE-DOMAIN/api/ios/office-dashboard`.
3. Add **Get Contents of URL** using that URL. Expand its options; set Method
   **GET**. Add header **Authorization** with value **Bearer YOUR_GENERATED_TOKEN**
   (one space after Bearer). Optionally add **Accept**: `application/json`.
   Put no token or user ID in the URL. Do not use Open URL/Safari for this request.
4. Add **Get Dictionary Value** with key `ok` from Contents of URL.
   Add **If** that value is true. Inside, add **Get Dictionary Value** with key
   `summary`, explicitly choosing the original **Contents of URL** output as its
   dictionary input. Add **Show Result** with that summary value.
5. In Otherwise, show `Dashboard unavailable. Check Railway configuration and
   the Authorization header.` End If. Network/TLS failures may stop Shortcuts
   before this branch; retry after checking connectivity.
6. Run once and allow access to your own service domain. Optionally use the
   Shortcut details menu to Add to Home Screen or add the Shortcuts widget.
   The dashboard fetches on each run; this ZIP contains no signed .shortcut file
   and does not provide a background-refresh widget or special iOS 27 API.

Treat the Shortcut as a credential: do not publish/share it with the token in it.
To revoke a lost device, rotate IOS_OFFICE_DASHBOARD_TOKEN, redeploy, then update
the private Shortcut. Use HTTPS; the bearer token itself grants office-wide read
access and is always bound server-side to ADMIN_USER_ID. This is an owner-only
integration, not staff login. Do not reuse a staff member's ID as the owner.

## Behavior and data limits

Only GET is supported. Missing/invalid credentials return 401; disabled or bad
configuration returns 503; inactive profile returns 403; query parameters return
400; authenticated HEAD/OPTIONS/POST/PUT/PATCH/DELETE return 405. No cookies or
query credentials are accepted, and no permissive CORS header is added. Responses
are JSON with no-store caching. Profile names, case rows and credentials are not
returned. Errors return generic messages and do not log exception contents.

This reuses `role_dashboard(admin_id, read_only=True)`. Profile/dashboard database
connections use PostgreSQL read-only transactions; schema creation is skipped.
Existing bot calls retain the default behavior. The service still logs into
Advocate Diaries to retrieve its existing read views; it does not invoke business
mutation endpoints. This is read-only business access, not a ban on upstream
authentication/session activity.

Counts retain the existing service's semantics: physical files dated today or
later; pending works from the live Advocate Diaries page when available, falling
back to local case_works; due-today/overdue from local case_works; pending hearing
updates from the existing Pending Cases parser. Some upstream failures are
silently handled by that existing service and can yield fallback/zero counts.
`generated_at` is UTC response-generation time, not a guarantee of source
freshness. Database CURRENT_DATE follows the existing database timezone; this
patch does not change timezone settings. Today's hearings are deliberately
omitted: next-date entries created today do not prove hearings scheduled today.

## Test and rollback

From repository root, with its Python environment and Flask installed:

```
python -m compileall -q attendance_app.py services/ios_office_dashboard.py services/role_intelligence_service.py tests/test_ios_office_dashboard.py
python -m unittest discover -s tests -p test_ios_office_dashboard.py -v
```

Tests use synthetic data and driver stubs; they do not contact a real database,
Advocate Diaries, Railway, Telegram, or production. In staging, confirm /health
still returns 200, no-header requests return 401 when enabled, and the private
Shortcut returns the expected counts. Verify upstream data against /officestatus.
Do not paste real tokens into public logs, browser URLs, screenshots, or issues.

Disable by setting IOS_OFFICE_DASHBOARD_ENABLED=false and redeploying. To remove,
restore the two backed-up existing files and remove the new endpoint module.
No database rollback is necessary.

References checked during preparation:
- Railway public networking: https://docs.railway.com/networking/public-networking
- Railway PORT: https://docs.railway.com/guides/deploying-a-monorepo
- Apple API requests: https://support.apple.com/en-ca/guide/shortcuts-mac/apd58d46713f/mac
- Apple dictionary extraction on iPhone: https://support.apple.com/en-nz/guide/shortcuts/apd9ba41d21b/ios
