# Staff De-linking + Advocate Diaries-Only Case Creation

## Changes

### Staff management

New administrator commands:

```text
/linkedstaff
/delinkstaff TELEGRAM_USER_ID
```

`/delinkstaff` also accepts an exact Advocate Diaries email or exact staff
name. Telegram ID is the safest option.

De-linking:

- sets `telegram_user_id` to NULL;
- sets `is_active` to FALSE;
- keeps the staff record and credentials available for later re-linking.

### New-case workflow

`/newcase` no longer starts a Telegram intake conversation.

It now directs the user to add the case in Advocate Diaries. The existing
scheduled Advocate Diaries synchronization will import it automatically.

The old `commands/new_case.py` file may remain in the repository for now, but
it is no longer registered by `case_handlers.py`.

## Upload

Replace these files in `LawOfficeBot-v2`:

```text
bot.py
handlers.py
case_handlers.py
commands/attendance.py
```

Commit and allow Railway to redeploy.

## Suggested commit

```text
Add staff de-linking and use Advocate Diaries for new cases
```

## Test

```text
/start
/newcase
/linkedstaff
/delinkstaff <Telegram ID>
/linkedstaff
```

Also verify that the de-linked staff member cannot use `/checkin` until
re-linked with `/linkstaff`.
