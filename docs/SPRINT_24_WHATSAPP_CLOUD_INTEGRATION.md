# Sprint 24 — WhatsApp Cloud Integration

## Delivered

- Existing manual `wa.me` review-and-send workflow retained as fallback.
- Meta WhatsApp Cloud API automatic sending.
- Approved-template support outside Meta's 24-hour customer-service window.
- Webhook verification and optional App Secret signature validation.
- Delivery, read and failure status updates.
- Inbound WhatsApp storage and duplicate protection.
- Automatic matching of inbound numbers to office cases.
- Telegram office/admin alert when a client message arrives.
- Admin inbox, diagnostics and real-message test commands.
- Failed-send retry queue every 10 minutes, limited to five attempts.

## Railway variables

```text
WHATSAPP_ENABLED=true
WHATSAPP_PHONE_NUMBER_ID=<Meta phone number ID>
WHATSAPP_BUSINESS_ACCOUNT_ID=<Meta WABA ID>
WHATSAPP_ACCESS_TOKEN=<permanent system-user access token>
WHATSAPP_VERIFY_TOKEN=<a long random secret chosen by you>
WHATSAPP_APP_SECRET=<Meta app secret>
WHATSAPP_GRAPH_VERSION=v23.0
WHATSAPP_TEMPLATE_LANGUAGE=en
```

For proactive messages, configure the approved templates actually created in
WhatsApp Manager. Each template used by this release must contain one body
text variable (`{{1}}`):

```text
WHATSAPP_TEMPLATE_CLIENT_WELCOME=law_office_client_welcome
WHATSAPP_TEMPLATE_NEW_CASE=law_office_new_case
WHATSAPP_TEMPLATE_CASE_STATUS=law_office_case_status
```

`WHATSAPP_DEFAULT_TEMPLATE` may be used as a fallback.

## Meta webhook

Callback URL:

```text
https://<your Railway public domain>/whatsapp/webhook
```

Verify token: the exact value of `WHATSAPP_VERIFY_TOKEN`.

Subscribe the WhatsApp Business Account to the `messages` webhook field.

## Bot commands

```text
/whatsappstatus
/testwhatsapp 919876543210
/whatsappinbox
/retrywhatsapp MESSAGE_ID
```

The first three administration commands are available only to
`ADMIN_USER_ID` in a private Telegram chat.

## Sending client communications

Existing commands remain:

```text
/welcomeclient CASE_NUMBER
/newcasewelcome CASE_NUMBER
/sendcasestatus CASE_NUMBER
```

When Cloud API is ready, the preview contains **Send Automatically**.
**Open WhatsApp** and **Mark Sent** remain available for manual fallback.

Free-form messages are used only when that number sent an inbound WhatsApp
within the last 24 hours. Otherwise the configured approved template is used.

## Production verification

1. Deploy with `WHATSAPP_ENABLED=false`.
2. Run `/whatsappstatus` and copy the displayed webhook URL.
3. Configure and verify the webhook in Meta.
4. Add the remaining Railway variables and set `WHATSAPP_ENABLED=true`.
5. Redeploy and run `/testwhatsapp` to a permitted test number.
6. Reply from that number and confirm:
   - Telegram receives a new-client-message alert;
   - `/whatsappinbox` shows the reply;
   - Meta delivery/read callbacks update the message record.
7. Create and approve the three templates before enabling proactive client
   messages in production.

## Security

- Never commit the access token or App Secret.
- Use a permanent Meta system-user token, not a short-lived developer token.
- Keep `WHATSAPP_APP_SECRET` configured so webhook signatures are enforced.
- Do not send confidential case material without client consent and a final
  human review.

## Rollback

Set `WHATSAPP_ENABLED=false`. Automatic sending and retries stop immediately,
while the existing manual `wa.me` workflow continues to work.
