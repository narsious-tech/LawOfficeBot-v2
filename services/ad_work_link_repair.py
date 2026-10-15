"""Owner-confirmed restoration of case context on orphaned imported tasks."""
import re
from dataclasses import asdict


def normalized(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def task_snapshot(task):
    return {key: str(task.get(key) or '') for key in
            ('id','task','assigned_to','case_number','notes','source_type','source_work_id','status')}


def suggest_case_context(task, records):
    """Exact work text proposes candidates only; owner must select and confirm."""
    if task.get('source_type') != 'advocate_diaries_work' or not normalized(task.get('task')):
        return []
    seen, result = set(), []
    for record in records:
        if normalized(record.description) != normalized(task['task']):
            continue
        if not record.case_title or not record.case_number or record.stable_key in seen:
            continue
        if normalized(task.get('case_number')) and normalized(task['case_number']) != normalized(record.case_number):
            continue
        if normalized(task.get('notes')) and normalized(task['notes']) != normalized(record.case_title):
            continue
        stored_id = str(task.get('source_work_id') or '').strip()
        if stored_id and stored_id != str(record.work_id or ''):
            continue
        seen.add(record.stable_key)
        result.append(asdict(record))
    return result


def load_orphans(task_id=None):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from config import DATABASE_URL
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT id,task,assigned_to,case_number,notes,source_type,source_work_id,status
                FROM tasks WHERE source_type='advocate_diaries_work'
                  AND UPPER(COALESCE(status,'PENDING'))='PENDING'
                  AND (NULLIF(TRIM(case_number),'') IS NULL OR NULLIF(TRIM(notes),'') IS NULL)
                  AND (%s IS NULL OR id=%s) ORDER BY id LIMIT 10""", (task_id,task_id))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def apply_confirmed_context(task_id, candidate, expected, actor_id, records):
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
    from config import DATABASE_URL
    # Revalidate the exact source case/work shown in the preview, even when AD
    # supplies no completion Work ID. Never manufacture a source_work_id.
    fields=('work_id','case_title','case_number','description','client')
    if not any(all(str(getattr(row,key) or '')==str(candidate.get(key) or '') for key in fields) for row in records):
        raise ValueError('The Advocate Diaries work changed or disappeared. Open a fresh preview.')
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task_id,))
            task=cur.fetchone()
            if not task or task_snapshot(task)!=expected or task.get('source_type')!='advocate_diaries_work' or str(task.get('status') or 'PENDING').upper()!='PENDING':
                raise ValueError('Task changed since preview. Open a fresh preview.')
            if not any(candidate == item for item in suggest_case_context(task, records)):
                raise ValueError('This candidate no longer matches the imported task.')
            work_id = str(candidate.get('work_id') or '').strip() or None
            if work_id:
                cur.execute("SELECT id FROM tasks WHERE source_type='advocate_diaries_work' AND source_work_id::text=%s AND id<>%s AND UPPER(COALESCE(status,'PENDING'))='PENDING' LIMIT 1", (work_id, task_id))
                if cur.fetchone():
                    raise ValueError('This AD Work ID already belongs to another pending task; no change made.')
            cur.execute('''CREATE TABLE IF NOT EXISTS ad_work_context_repairs(
                id BIGSERIAL PRIMARY KEY, task_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
                before_values JSONB NOT NULL, confirmed_candidate JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
            cur.execute("""UPDATE tasks SET
                case_number=COALESCE(NULLIF(TRIM(case_number),''),%s),
                notes=COALESCE(NULLIF(TRIM(notes),''),%s),
                source_work_id=COALESCE(NULLIF(TRIM(source_work_id::text),''),%s)
                WHERE id=%s RETURNING case_number,notes,source_work_id""",
                (candidate['case_number'], candidate['case_title'], work_id, task_id))
            result=dict(cur.fetchone())
            cur.execute('INSERT INTO ad_work_context_repairs(task_id,actor_id,before_values,confirmed_candidate) VALUES (%s,%s,%s,%s)', (task_id,actor_id,Json(expected),Json(candidate)))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
