"""Private, scoped WhatsApp date review with expiring confirmation tokens."""
import secrets
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from config import DATABASE_URL
from services.ecourts_date_access import can_manage_ecourts_dates, date_snapshot


def _schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS whatsapp_ecourts_date_confirmations (
        token TEXT PRIMARY KEY, actor_id BIGINT NOT NULL, verification_id BIGINT NOT NULL,
        decision TEXT NOT NULL, expected_snapshot JSONB NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '10 minutes',
        consumed_at TIMESTAMPTZ)''')


def _conflict_text(item):
    return (f"⚖️ eCOURTS DATE REVIEW\n{item.get('case_title') or 'Title not recorded'}\n"
            f"Case: {item.get('display_case_number')}\nCNR: {item.get('cino')}\n"
            f"Staff / Advocate Diaries: {item.get('staff_next_date')}\n"
            f"eCourts: {item.get('ecourts_next_date')}\n"
            f"Purpose: {item.get('ecourts_purpose') or '-'}\n\n"
            "Accept requests an Advocate Diaries correction. Keep retains the staff date.")


def handle_date_inbound(item):
    action = str(item.get('action_id') or '')
    text = str(item.get('text') or '').strip()
    if text.upper() not in {'DATES', 'ECOURTS DATES', '/ECOURTSDATECHECK', '/ECOURTSDATES'} and not action.startswith('ecd:'):
        return None
    from services.whatsapp_cloud import normalize_phone
    phone = normalize_phone(str(item.get('phone') or ''))
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT telegram_user_id,staff_name,role FROM staff_accounts WHERE whatsapp_phone=%s AND COALESCE(is_active,TRUE)=TRUE', (phone,))
            staff = cur.fetchone()
        if not staff or not can_manage_ecourts_dates(staff['telegram_user_id']):
            return {'phone': phone, 'reply': '⛔ This date desk is restricted to Priya’s authorised linked account.', 'buttons': []}
        actor = int(staff['telegram_user_id'])
        result = {'phone': phone, 'staff': dict(staff), 'incoming': text or action, 'buttons': []}
        from services.ecourts_date_verification_service import list_date_conflicts, reconcile_date_verifications, review_date_conflict
        if action.startswith('ecd:confirm:'):
            token = action.split(':', 2)[2]
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                _schema(cur)
                cur.execute('SELECT * FROM whatsapp_ecourts_date_confirmations WHERE token=%s AND actor_id=%s AND consumed_at IS NULL AND expires_at>NOW() FOR UPDATE', (token, actor))
                confirmation = cur.fetchone()
                if not confirmation:
                    result['reply'] = '⚠️ Confirmation expired or already used. Send DATES to review again.'
                    return result
                # Consume before writeback; repeats cannot submit a second correction.
                cur.execute('UPDATE whatsapp_ecourts_date_confirmations SET consumed_at=NOW() WHERE token=%s', (token,))
                conn.commit()
            decision = review_date_conflict(int(confirmation['verification_id']), confirmation['decision'], actor, expected_snapshot=confirmation['expected_snapshot'])
            result['reply'] = (f"✅ DATE DECISION RECORDED\nCase: {decision.get('display_case_number')}\n"
                               f"Decision: {decision['decision']}\n{decision.get('message') or ''}\n"
                               f"Advocate Diaries: {decision.get('ad_sync_status') or 'NOT_REQUIRED'}\n"
                               f"{decision.get('ad_sync_message') or ''}\nSend DATES for the next case.")
            result['incoming'] = f"DATE DECISION: {decision.get('display_case_number')} — {decision['decision']} — AD: {decision.get('ad_sync_status') or 'NOT_REQUIRED'}"
            return result
        if action.startswith('ecd:choose:'):
            parts = action.split(':')
            decisions = {'accept': 'ACCEPT_ECOURTS', 'keep': 'KEEP_STAFF', 'later': 'REVIEW_LATER'}
            if len(parts) != 4 or not parts[3].isdigit() or parts[2] not in decisions:
                raise ValueError('Invalid date decision reference.')
            row = next((row for row in list_date_conflicts(100) if int(row['id']) == int(parts[3])), None)
            if not row:
                raise ValueError('This conflict is no longer pending. Send DATES to refresh.')
            token = secrets.token_urlsafe(18)
            with conn.cursor() as cur:
                _schema(cur)
                cur.execute('INSERT INTO whatsapp_ecourts_date_confirmations(token,actor_id,verification_id,decision,expected_snapshot) VALUES (%s,%s,%s,%s,%s)', (token, actor, int(row['id']), decisions[parts[2]], Json(date_snapshot(row))))
                conn.commit()
            result['reply'] = _conflict_text(row) + '\n\nConfirm decision: ' + decisions[parts[2]]
            result['buttons'] = [(f'ecd:confirm:{token}', 'Confirm'), ('ecd:cancel', 'Cancel')]
            return result
        if action == 'ecd:cancel':
            with conn.cursor() as cur:
                _schema(cur)
                cur.execute('UPDATE whatsapp_ecourts_date_confirmations SET consumed_at=NOW() WHERE actor_id=%s AND consumed_at IS NULL', (actor,))
                conn.commit()
            result['reply'] = 'Cancelled. No date was changed. Send DATES to return.'
            return result
        if action and action != 'ecd:desk':
            raise ValueError('Unknown date control. Send DATES.')
        reconcile_date_verifications(None)
        rows = list_date_conflicts(1)
        if not rows:
            result['reply'] = '✅ No next-date conflict awaits a decision. Delayed eCourts records remain awaiting publication.'
        else:
            row = rows[0]
            result['reply'] = _conflict_text(row)
            result['buttons'] = [(f"ecd:choose:accept:{row['id']}", 'Accept eCourts'), (f"ecd:choose:keep:{row['id']}", 'Keep Staff Date'), (f"ecd:choose:later:{row['id']}", 'Review Later')]
        return result
    except Exception as exc:
        conn.rollback()
        return {'phone': phone, 'reply': f'⚠️ Date review could not complete: {str(exc)[:500]}\nSend DATES to refresh. If a correction was confirmed, check its recorded status before retrying.', 'buttons': []}
    finally:
        conn.close()
