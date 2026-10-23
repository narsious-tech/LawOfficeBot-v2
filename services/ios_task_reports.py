"""Owner reports without invoking task repair, sync, assignment or completion."""
from datetime import date, datetime, time, timezone
import logging
import os
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
logger = logging.getLogger(__name__)
PRIORITY = {'URGENT': 0, 'HIGH': 1, 'NORMAL': 2, 'LOW': 3}


def _text(value, fallback='Not recorded'):
    return ' '.join(str(value or '').split()) or fallback


def _deadline(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            parsed = None
            for fmt in ('%d-%m-%Y %I:%M %p', '%d-%m-%Y %H:%M', '%d-%m-%Y', '%d/%m/%Y'):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)


def _read_local_tasks():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    con = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=10)
    try:
        con.set_session(readonly=True)
        with con.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema='public' AND table_name='tasks'""")
            columns = {r['column_name'] for r in cur.fetchall()}
            if not {'id', 'task', 'status'}.issubset(columns):
                raise RuntimeError('Local task schema unavailable')
            # Only hard-coded identifiers may become SQL. Optional legacy fields
            # are explicit NULLs, not a reason to initialize or repair the schema.
            fields = ('id', 'task', 'status', 'assigned_to', 'case_number', 'notes',
                      'source_type', 'source_work_id', 'priority', 'due_at', 'deadline')
            selection = ', '.join(f'"{field}"' if field in columns else f'NULL AS "{field}"'
                                  for field in fields)
            cur.execute(f"""SELECT {selection} FROM public.tasks
                            WHERE UPPER(TRIM(COALESCE(status, 'PENDING')))='PENDING'
                            ORDER BY id""")
            return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def _read_live_works():
    from advocate_web import AdvocateWeb
    from services.ad_work_service import parse_works_html

    web = AdvocateWeb()
    response = web.works('pending')
    if response.status_code != 200 or web._is_login_response(response):
        raise RuntimeError('Live works unavailable')
    if urlparse(str(response.url)).path.rstrip('/') != '/works':
        raise RuntimeError('Unexpected live works page')
    if 'html' not in str(response.headers.get('Content-Type', '')).lower():
        raise RuntimeError('Unexpected live works content')
    records = parse_works_html(response.text)
    if not records:
        # Do not turn a changed/error HTML page into a false zero-work success.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'lxml')
        text = soup.get_text(' ', strip=True).lower()
        empty_message = any(s in text for s in ('no works found', 'no work found',
                                               'no records found', 'no data available'))
        table = soup.find('table')
        empty_table = (table is not None and 'work' in table.get_text(' ', strip=True).lower()
                       and table.find('tbody') is not None and not table.find('tbody').find_all('tr'))
        if not empty_message and not empty_table:
            raise RuntimeError('Live works layout unrecognized')
    return records


def _prepare(rows, today):
    prepared = []
    for row in rows:
        item = dict(row)
        # A valid due_at takes precedence; an invalid due_at may use deadline.
        due = _deadline(row.get('due_at')) or _deadline(row.get('deadline'))
        item['_due'] = due
        item['_priority'] = _text(row.get('priority'), 'NORMAL').upper()
        item['_bucket'] = ('undated' if due is None else 'overdue' if due.date() < today
                           else 'due_today' if due.date() == today else 'future')
        prepared.append(item)
    return sorted(prepared, key=lambda r: (
        PRIORITY.get(r['_priority'], 2),
        r['_due'] or datetime.max.replace(tzinfo=IST), str(r.get('id') or '')))


def _task_lines(rows):
    lines = []
    for index, row in enumerate(rows, 1):
        due = row['_due']
        lines.extend([
            f"{index}. Task #{row['id']} | {row['_priority']}",
            f"Staff: {_text(row.get('assigned_to'), 'Unassigned')}",
            f"Case: {_text(row.get('case_number'))}",
            f"Work: {_text(row.get('task'))}",
            f"Due: {due:%d-%m-%Y %H:%M} IST" if due else 'Due: Not recorded or unrecognized',
            'Source: imported AD work (local task)' if row.get('source_type') == 'advocate_diaries_work'
            else 'Source: local task',
            '',
        ])
    return lines


def build_task_report(kind):
    if kind not in ('office_tasks', 'overdue', 'due_today', 'deadlines'):
        raise ValueError('Unknown task report')
    now = datetime.now(IST)
    today = now.date()
    warnings = []
    local_status = 'available'
    try:
        tasks = _prepare(_read_local_tasks(), today)
    except Exception as exc:
        logger.error('iOS task source failed: local_tasks type=%s', type(exc).__name__)
        tasks = []
        local_status = 'unavailable'
        warnings.append('Local tasks unavailable. Task counts and deadline lists could not be verified.')

    buckets = {name: [t for t in tasks if t['_bucket'] == name]
               for name in ('overdue', 'due_today', 'future', 'undated')}
    counts = ({'pending': len(tasks), **{key: len(value) for key, value in buckets.items()}}
              if local_status == 'available' else None)
    title = {'office_tasks': 'OFFICE TASKS', 'overdue': 'OVERDUE TASKS',
             'due_today': 'TASKS DUE TODAY', 'deadlines': 'OVERDUE / DUE TODAY'}[kind]
    lines = [title, f'Date: {today:%d-%m-%Y} | Refreshed: {now:%H:%M} IST', '']
    live_status = 'not_requested'
    live_count = None
    if kind == 'office_tasks':
        records = []
        try:
            records = _read_live_works()
            live_status = 'available'
            live_count = len(records)
        except Exception as exc:
            logger.error('iOS task source failed: advocate_diaries_works type=%s', type(exc).__name__)
            live_status = 'unavailable'
            warnings.append('Live Advocate Diaries works unavailable. Local task data is not a substitute for the live work list.')
        lines.append('LOCAL PENDING TASKS')
        if counts is not None:
            lines.extend([f"Pending: {counts['pending']} | Overdue: {counts['overdue']} | Due today: {counts['due_today']}",
                          f"Future: {counts['future']} | No usable deadline: {counts['undated']}", ''])
            lines.extend(_task_lines(tasks) if tasks else ['No local pending tasks.', ''])
        else:
            lines.extend(['Unavailable; not counted as zero.', ''])
        lines.extend(['LIVE ADVOCATE DIARIES PENDING WORKS',
                      'This is a separate source. Imported local tasks may refer to the same work; do not add the counts.', ''])
        if live_status == 'available':
            lines.append(f'Live works: {live_count}')
            # Link metadata by authoritative source_work_id only; never by similar text.
            links = {}
            for task in tasks:
                if task.get('source_type') == 'advocate_diaries_work' and task.get('source_work_id'):
                    links.setdefault(str(task['source_work_id']), []).append(task)
            for index, record in enumerate(records, 1):
                linked = links.get(str(record.work_id), []) if record.work_id else []
                lines.extend([f'{index}. AD work #{record.work_id or "ID unavailable"}',
                              f'Client: {_text(record.client)}', f'Case: {_text(record.case_number)}',
                              f'Title: {_text(record.case_title)}', f'Work: {_text(record.description)}'])
                if linked:
                    lines.append('Linked local task(s): ' + ', '.join('#' + str(t['id']) for t in linked))
                    lines.append('Locally assigned to: ' + ', '.join(sorted({
                        _text(t.get('assigned_to'), 'Unassigned') for t in linked})))
                else:
                    lines.append('Local assignment: unavailable' if local_status != 'available'
                                 else 'No matching pending local task')
                lines.extend(['Work deadline: not supplied by this live source; use linked local task deadlines.', ''])
            if not records:
                lines.append('No live pending works listed.')
        else:
            lines.append('Unavailable; not counted as zero.')
    elif local_status == 'available':
        lines.extend([f"Total local pending tasks: {counts['pending']}",
                      f"No usable deadline: {counts['undated']} (excluded from date buckets)",
                      'Date buckets use IST calendar days; a task due earlier today stays Due Today.', ''])
        wanted = ('overdue', 'due_today') if kind == 'deadlines' else (kind,)
        for bucket in wanted:
            rows = buckets[bucket]
            lines.extend([('OVERDUE' if bucket == 'overdue' else 'DUE TODAY') + f' — {len(rows)}', ''])
            lines.extend(_task_lines(rows) if rows else ['No matching local pending tasks.', ''])
        lines.append('Source: local tasks only. No deadline is inferred from a hearing date.')
    else:
        lines.append('Deadline report unavailable; local tasks could not be read.')

    if warnings:
        lines = lines[:2] + ['', 'SOURCE WARNINGS', *warnings, ''] + lines[2:]
    ok = local_status == 'available' or (kind == 'office_tasks' and live_status == 'available')
    return {
        'ok': ok, 'report': kind, 'report_date': today.isoformat(), 'timezone': 'Asia/Kolkata',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'sources': {'local_tasks': local_status, 'advocate_diaries_works': live_status},
        'partial': ok and bool(warnings), 'local_counts': counts, 'live_works_count': live_count,
        'warnings': warnings, 'summary': '\n'.join(lines),
    }
