"""Private staff delivery with durable, free-window WhatsApp catch-up."""
import hashlib
import logging
import re
import html
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

logger = logging.getLogger(__name__)


def plain_text(text):
    return html.unescape(re.sub(r'</?(?:b|i|code|pre)>', '', text))


def staff_recipients(names=None):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT telegram_user_id,staff_name,whatsapp_phone FROM staff_accounts WHERE COALESCE(is_active,TRUE)=TRUE')
            rows = [dict(row) for row in cur.fetchall()]
        wanted = {name.strip().casefold() for name in names} if names is not None else None
        return [row for row in rows if wanted is None or str(row['staff_name']).strip().casefold() in wanted]
    finally:
        conn.close()


def _schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS whatsapp_staff_notification_queue (
        id BIGSERIAL PRIMARY KEY, phone TEXT NOT NULL, event_key TEXT NOT NULL,
        body TEXT NOT NULL, next_chunk INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL, sent_at TIMESTAMPTZ,
        UNIQUE(phone,event_key))''')


def flush_staff_notifications(phone):
    from services.whatsapp_cloud import normalize_phone, send_text_message
    from services.whatsapp_morning_delivery_service import split_whatsapp_message
    phone = normalize_phone(phone)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _schema(cur)
            conn.commit()
            cur.execute("SELECT 1 FROM whatsapp_inbound_messages WHERE sender_phone=%s AND received_at>=NOW()-INTERVAL '24 hours' LIMIT 1", (phone,))
            if not cur.fetchone():
                return 'QUEUED'
            cur.execute('SELECT id FROM whatsapp_staff_notification_queue WHERE phone=%s AND sent_at IS NULL AND expires_at>NOW() ORDER BY id LIMIT 10', (phone,))
            ids = [row['id'] for row in cur.fetchall()]
            for row_id in ids:
                # Session lock spans progress commits, excluding concurrent webhook deliveries.
                cur.execute('SELECT pg_try_advisory_lock(%s) AS locked', (row_id,))
                if not cur.fetchone()['locked']:
                    continue
                try:
                    cur.execute('SELECT * FROM whatsapp_staff_notification_queue WHERE id=%s AND sent_at IS NULL AND expires_at>NOW()', (row_id,))
                    row = cur.fetchone()
                    if not row:
                        continue
                    chunks = split_whatsapp_message(row['body'])
                    for index in range(row['next_chunk'], len(chunks)):
                        send_text_message(phone, chunks[index])
                        cur.execute('UPDATE whatsapp_staff_notification_queue SET next_chunk=%s WHERE id=%s', (index + 1, row_id))
                        conn.commit()
                    cur.execute('UPDATE whatsapp_staff_notification_queue SET sent_at=NOW() WHERE id=%s', (row_id,))
                    conn.commit()
                finally:
                    conn.rollback()
                    cur.execute('SELECT pg_advisory_unlock(%s)', (row_id,))
                    conn.commit()
            return 'SENT'
    finally:
        conn.close()


def deliver_staff_whatsapp(text, event_key, names=None, expires_at=None):
    from services.whatsapp_cloud import normalize_phone
    result = {'sent': [], 'queued': [], 'missing': [], 'failed': []}
    for staff in staff_recipients(names):
        name, phone = staff['staff_name'], staff.get('whatsapp_phone')
        if not phone:
            result['missing'].append(name)
            continue
        conn = None
        try:
            phone = normalize_phone(phone)
            stored_key = hashlib.sha256(str(event_key).encode()).hexdigest()
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
            with conn.cursor() as cur:
                _schema(cur)
                cur.execute('''INSERT INTO whatsapp_staff_notification_queue(phone,event_key,body,expires_at)
                    VALUES (%s,%s,%s,COALESCE(%s::timestamptz,NOW()+INTERVAL '3 days'))
                    ON CONFLICT(phone,event_key) DO NOTHING''', (phone, stored_key, plain_text(text), expires_at))
            conn.commit()
            status = flush_staff_notifications(phone)
            result['queued' if status == 'QUEUED' else 'sent'].append(name)
        except Exception:
            logger.exception('Staff WhatsApp delivery failed for %s', name)
            result['failed'].append(name)
        finally:
            if conn:
                conn.close()
    return result


def file_event_key(target, text):
    return 'files:' + str(target) + ':' + hashlib.sha256(plain_text(text).encode()).hexdigest()


async def notify_ecourts_staff(context, text, event_key):
    import asyncio
    names = ('Priya', 'Preet')
    try:
        recipients = await asyncio.to_thread(staff_recipients, names)
        for staff in recipients:
            try:
                if staff.get('telegram_user_id'):
                    for start in range(0, len(plain_text(text)), 3900):
                        await context.bot.send_message(chat_id=int(staff['telegram_user_id']), text=plain_text(text)[start:start+3900])
            except Exception:
                logger.exception('Private eCourts Telegram delivery failed for %s', staff['staff_name'])
        result = await asyncio.to_thread(deliver_staff_whatsapp, text, event_key, names)
        logger.info('eCourts staff WhatsApp delivery: %s', result)
    except Exception:
        logger.exception('eCourts staff notifications failed; sync result is preserved')
