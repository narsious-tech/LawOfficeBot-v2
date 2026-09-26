# Meta publication URLs

Deploy the included `attendance_app.py` and three templates, then use:

- App domain: `lawofficebot-v2-production.up.railway.app`
- Privacy Policy URL: `https://lawofficebot-v2-production.up.railway.app/privacy`
- Terms of Service URL: `https://lawofficebot-v2-production.up.railway.app/terms`
- User data deletion: select **Data deletion instructions URL** and enter
  `https://lawofficebot-v2-production.up.railway.app/data-deletion`

Optional Railway variables:

```text
OFFICE_NAME=Law Office of Ajay Chawla
PRIVACY_CONTACT_EMAIL=your monitored office email
```

The routes do not activate WhatsApp messaging and are safe to deploy while
`WHATSAPP_ENABLED=false` and `WHATSAPP_STAFF_COMPANION_ENABLED=false`.
