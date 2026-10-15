"""Fill missing imported-work case context by authoritative AD Work ID only."""
import logging
import threading
import time

_lock = threading.Lock()
_last_attempt = 0.0
logger = logging.getLogger(__name__)


def repair_work_case_details(records=None):
    global _last_attempt
    # A shared cache avoids fetching the AD work list separately for every staff list.
    with _lock:
        if records is None and time.monotonic() - _last_attempt < 900:
            return {'updated': 0, 'skipped': 'cached'}
        _last_attempt = time.monotonic()
        conn = None
        try:
            import psycopg2
            from config import DATABASE_URL
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
            with conn.cursor() as cur:
                cur.execute("""SELECT DISTINCT source_work_id::text FROM tasks
                    WHERE source_type='advocate_diaries_work' AND source_work_id IS NOT NULL
                      AND UPPER(COALESCE(status,'PENDING'))='PENDING'
                      AND (NULLIF(TRIM(case_number),'') IS NULL OR NULLIF(TRIM(notes),'') IS NULL)""")
                ids = {str(row[0]) for row in cur.fetchall()}
                if not ids:
                    return {'updated': 0}
                if records is None:
                    from advocate_web import AdvocateWeb
                    from services.ad_work_service import fetch_works
                    records, status = fetch_works(AdvocateWeb(), 'pending')
                    if status != 200:
                        raise RuntimeError(f'AD work fetch returned {status}')
                updated = 0
                for record in records:
                    if not record.work_id or str(record.work_id) not in ids:
                        continue
                    number, title = record.case_number.strip(), record.case_title.strip()
                    if not number and not title:
                        continue
                    cur.execute("""UPDATE tasks SET
                        case_number=COALESCE(NULLIF(TRIM(case_number),''),NULLIF(%s,'')),
                        notes=COALESCE(NULLIF(TRIM(notes),''),NULLIF(%s,''))
                        WHERE source_type='advocate_diaries_work' AND source_work_id::text=%s
                          AND UPPER(COALESCE(status,'PENDING'))='PENDING'
                          AND ((NULLIF(TRIM(case_number),'') IS NULL AND %s<>'')
                            OR (NULLIF(TRIM(notes),'') IS NULL AND %s<>''))""",
                        (number, title, str(record.work_id), number, title))
                    updated += cur.rowcount
            conn.commit()
            return {'updated': updated}
        except Exception:
            if conn:
                conn.rollback()
            logger.exception('AD work case context repair failed; assignments and completion remain unchanged')
            return {'updated': 0, 'skipped': 'unavailable'}
        finally:
            if conn:
                conn.close()
