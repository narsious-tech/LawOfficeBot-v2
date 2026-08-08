AJAY AI — PROVIDER RESILIENCE PACK
==================================

DROP-IN CHANGED FILES
---------------------
1. ai/config.py
2. ai/gateway.py

Railway reference:
3. RAILWAY_VARIABLES.example

WHAT THIS RELEASE DOES
----------------------
Ajay AI now follows this recovery chain:

  selected Gemini model
      -> retry transient failures with exponential backoff
      -> Gemini fallback model(s)
      -> OpenAI emergency fallback
      -> only then return "Ajay AI unavailable"

Transient Gemini failures retried:
- HTTP 429 quota/rate-limit response
- HTTP 5xx, including the HTTP 503 currently seen in Telegram
- timeout/network failures

Non-transient Gemini errors such as 400/401/403/404 do NOT waste time retrying
the same endpoint; the gateway moves to the next configured Gemini model.

SAFETY / BEHAVIOUR
------------------
- Existing Ajay AI prompts, OfficeKnowledgeService, case intelligence,
  hearing intelligence, session storage and usage logging remain intact.
- No API keys are included in this package.
- Successful usage is logged against the model that actually answered.
- The existing Telegram AIUnavailable handling remains compatible.

DEPLOYMENT
----------
Replace:
  ai/config.py
  ai/gateway.py

Then set/confirm Railway variables from RAILWAY_VARIABLES.example.

Recommended:
  AI_PROVIDER=gemini
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_PRO_MODEL=gemini-2.5-pro
  GEMINI_FALLBACK_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
  AI_RETRY_ATTEMPTS=3
  AI_RETRY_BASE_SECONDS=1.0
  AI_OPENAI_FALLBACK_ENABLED=true
  OPENAI_MODEL=gpt-5.5

Keep GEMINI_API_KEY and OPENAI_API_KEY only in Railway Variables.

TEST AFTER RAILWAY REDEPLOY
---------------------------
1. /ai
2. Case Intelligence
3. Select BA/6952/2026 (or another known case)
4. Confirm a case brief is returned.
5. Check Railway logs. A temporary Gemini 503 should be retried automatically;
   if Gemini remains unavailable and OpenAI fallback is enabled/configured,
   the request should continue through OpenAI instead of immediately failing.

ROLLBACK
--------
Restore the previous ai/config.py and ai/gateway.py from GitHub history.
