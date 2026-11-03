# Sprint 51.3 — Heavy Dates in the iPhone Office Dashboard

## Railway

Keep the existing `ADMIN_USER_ID`. Add one private secret if it is not already present:

```text
IOS_DASHBOARD_TOKEN=<a private random value of at least 32 characters>
```

Never place this token in a URL, screenshot, GitHub file or shared Shortcut.

## Shortcut menu

Edit **Daily Office Dashboard** and add two menu entries.

### Heavy Dates

1. Add **URL**:
   `https://lawofficebot-v2-production.up.railway.app/api/ios/heavy-dates?days=45`
2. Add **Get Contents of URL**:
   - Method: `GET`
   - Header: `Authorization`
   - Value: `Bearer YOUR_IOS_DASHBOARD_TOKEN`
3. Add **Get Dictionary Value**: key `display_text`.
4. Add **Show Result** using that value.

### Heavy Date Details

1. Add **Ask for Input**:
   - Prompt: `Enter hearing date (DD-MM-YYYY)`
   - Input type: Text
2. Add **URL** and enter the base URL:
   `https://lawofficebot-v2-production.up.railway.app/api/ios/heavy-date`
3. In the URL action, expand the query section and add:
   - Key: `date`
   - Value: the **Provided Input** magic variable
4. Add **Get Contents of URL** with the same authorization header.
5. Add **Get Dictionary Value**: key `display_text`.
6. Add **Show Result** using that value.

The API is read-only. It uses the same heavy-date service as Telegram and WhatsApp,
so duplicate merging and purpose grouping remain identical on every channel.
