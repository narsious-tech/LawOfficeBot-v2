# Sprint 42 — WhatsApp Client Registry and Advocate Diaries Mobile Sync

## Owner command

Use the command only in Ajay's private Telegram chat:

```text
/whatsappclients
/whatsappclients CLIENT NAME
/whatsappclients CASE_NUMBER
/whatsappclients LAST_FOUR_DIGITS
```

The paginated list masks every number. Selecting a client opens the full number,
source, Advocate Diaries client ID and linked cases. The `wac:` callbacks and the
command are restricted by the central access policy to the configured administrator.

## Automatic Advocate Diaries synchronization

The daily 8:45 AM IST job now runs Advocate Diaries Sync v3 instead of the older
case-only sync. Sync v3 fetches each unique client's details and recognizes common
API phone fields including `primary_phone`, `mobile`, `mobile_number`, `phone`,
`whatsapp_number`, and their common alternatives.

After an Advocate Diaries client number is entered:

1. The next 8:45 AM sync imports it into the local `clients` record.
2. All linked cases with blank mobile values are repaired automatically.
3. The number becomes available to the WhatsApp Client Reception verification flow.
4. `/whatsappclients` shows Advocate Diaries as the source.

Run `/synccasesv3` for an immediate manual refresh instead of waiting for the daily job.

## Conflict safety

- Blank Office OS numbers are filled automatically from Advocate Diaries.
- Identical numbers are retained without duplicate changes.
- If an existing Office OS/WhatsApp number differs from Advocate Diaries, the local
  number is preserved and the difference is recorded in `client_mobile_sync_audit`.
- Use the **Mobile conflicts** button inside `/whatsappclients` to review differences.
- The sync never broadcasts client numbers to a group.
