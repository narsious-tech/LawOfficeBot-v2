# AILIP v1.0.0 — Ajay AI Foundation

## Included

- Private `/ai` Telegram workspace.
- OpenAI Responses API gateway.
- Versioned prompt files.
- Read-only bounded Office Knowledge Service.
- AI sessions, messages and usage audit tables.
- Safe feature flag and administrator allow-list.
- First functional capabilities: **Ask Ajay AI** and preliminary **Case Intelligence** from the local `cases` table.

## Railway variables

Set:

```text
AI_ENABLED=true
OPENAI_API_KEY=<your secret API key>
OPENAI_MODEL=gpt-5.5
AI_ADMIN_USER_IDS=<your Telegram numeric user ID>
AI_MAX_OUTPUT_TOKENS=1800
AI_TEMPERATURE=0.2
AI_TIMEOUT_SECONDS=90
```

`AI_ADMIN_USER_IDS` accepts comma-separated IDs. Do not put staff IDs there yet.

## Deployment

1. Deploy this package to a staging Railway service first.
2. Add the variables above. Keep `AI_ENABLED=false` for the first boot.
3. Confirm the bot starts and existing `/start`, `/office`, hearing and file workflows still operate.
4. Change `AI_ENABLED=true`.
5. Run `/ai` from the authorized Telegram account.
6. Test **Ask Ajay AI** with a harmless question.
7. Test **Case Intelligence** using a known local case number.

The AI schema is created idempotently on the first authorized request. The SQL migration is also supplied for controlled manual deployment.

## Security and legal controls

- Access is denied unless the Telegram user ID is allow-listed.
- AI does not receive database credentials or unrestricted SQL access.
- The first knowledge service reads only selected fields from the local `cases` table and returns at most five matches.
- Every response carries a verification warning.
- Usage and failures are logged without exposing the OpenAI key.

## Rollback

Set `AI_ENABLED=false`. The Office OS remains available. For a complete code rollback, deploy the prior known-good release. The new `ai_*` tables may remain because they do not alter existing operational tables.

## Known limits

- This foundation does not perform live web case-law research.
- It does not yet read Google Drive documents or Advocate Diaries hearing timelines.
- Case Intelligence is preliminary and based on local `cases` data only.
- Cost values are not estimated in currency; token usage is recorded when supplied by the API.
