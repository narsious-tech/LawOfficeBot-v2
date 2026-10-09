"""Scoped eCourts date delegation for Priya's linked account."""
import os

DATE_COMMANDS = {'ecourtsdatecheck', 'ecourtsdates'}
DATE_CALLBACKS = {'datecheck', 'dateaccept', 'datekeep', 'datelater'}


def delegated_user_id():
    value = os.getenv('ECOURTS_DATE_MANAGER_TELEGRAM_ID', '8413754577').strip()
    return int(value) if value.isdigit() else None


def is_date_action(command=None, callback=None):
    if command is not None:
        return command.lower().lstrip('/').split('@')[0] in DATE_COMMANDS
    parts = str(callback or '').split(':')
    return len(parts) >= 2 and parts[0] == 'ecr' and parts[1] in DATE_CALLBACKS


def can_manage_ecourts_dates(user_id):
    if user_id is None or int(user_id) != delegated_user_id():
        return False
    from config import DATABASE_URL
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM staff_accounts WHERE telegram_user_id=%s AND COALESCE(is_active,TRUE)=TRUE', (int(user_id),))
            return bool(cur.fetchone())
    finally:
        conn.close()


def date_snapshot(item):
    return {key: str(item.get(key) or '') for key in (
        'staff_next_date', 'ecourts_next_date', 'staff_last_date',
        'ecourts_last_date', 'source_sync_run_id', 'local_case_pk', 'cino',
    )}
