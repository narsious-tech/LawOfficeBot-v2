"""Read-only attendance view using the same sessions as /whoinoffice."""
from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def _read_sessions(today):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    con = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=10)
    try:
        con.set_session(readonly=True)
        with con.cursor(cursor_factory=RealDictCursor) as cur:
            # to_jsonb allows the optional transfer field on older schemas.
            # No coordinates, Telegram IDs or location history leave this reader.
            cur.execute("""
                SELECT staff_name, checkin_time, checkin_office_name,
                       checkout_time, checkout_office_name, status, working_minutes,
                       COALESCE(NULLIF(TRIM(to_jsonb(s)->>'current_office_name'), ''),
                                checkin_office_name) AS current_office_name
                FROM attendance_sessions s
                WHERE attendance_date=%s
                ORDER BY checkin_time ASC NULLS LAST, staff_name ASC
            """, (today,))
            return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


def _text(value, fallback='Not recorded'):
    return ' '.join(str(value or '').split()) or fallback


def _ist(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def _clock(value):
    value = _ist(value)
    return value.strftime('%I:%M %p') if value else 'Not recorded'


def _duration(start, end):
    start, end = _ist(start), _ist(end)
    if start is None or end is None or end < start:
        return 'Unavailable'
    minutes = int((end - start).total_seconds() // 60)
    return f'{minutes // 60}h {minutes % 60}m'


def _now():
    return datetime.now(IST)


def build_who_in_office_report():
    now = _now()
    today = now.date()
    sessions = _read_sessions(today)
    working, checked_out, uncertain = [], [], []
    cancelled = 0
    for row in sessions:
        status = str(row.get('status') or '').strip().upper()
        start, end = _ist(row.get('checkin_time')), _ist(row.get('checkout_time'))
        if status == 'CANCELLED':
            cancelled += 1
        elif status == 'OPEN' and start is not None and end is None and row.get('checkout_time') is None and start <= now:
            working.append(row)
        elif status == 'CLOSED' and start is not None and end is not None and start <= end <= now:
            checked_out.append(row)
        else:
            uncertain.append(row)

    lines = ["WHO'S IN OFFICE", f'Date: {today:%d-%m-%Y} | Refreshed: {now:%I:%M %p} IST',
             f'Currently working (open attendance): {len(working)}',
             f'Checked out today: {len(checked_out)}', '']
    if not sessions:
        lines.extend(['No attendance sessions recorded for today.', ''])
    lines.extend(['CURRENTLY WORKING', ''])
    if not working:
        lines.extend(['No valid open attendance sessions recorded for today.', ''])
    for row in working:
        lines.extend([_text(row.get('staff_name'), 'Unnamed staff'),
                      'Current recorded office: ' + _text(row.get('current_office_name') or row.get('checkin_office_name')),
                      'Checked in: ' + _clock(row.get('checkin_time')),
                      'Elapsed since check-in: ' + _duration(row.get('checkin_time'), now), ''])
    if checked_out:
        lines.extend(['CHECKED OUT TODAY', ''])
        for row in checked_out:
            lines.extend([_text(row.get('staff_name'), 'Unnamed staff'),
                          f"In: {_clock(row.get('checkin_time'))} | Out: {_clock(row.get('checkout_time'))}",
                          'Checkout office: ' + _text(row.get('checkout_office_name')),
                          'Elapsed check-in to checkout: ' + _duration(row.get('checkin_time'), row.get('checkout_time')), ''])
    warnings = []
    if uncertain:
        warnings.append(f'{len(uncertain)} attendance record(s) need verification; excluded from working/checked-out counts.')
        lines.extend(['NEEDS VERIFICATION', warnings[0]])
        for row in uncertain:
            lines.append(_text(row.get('staff_name'), 'Unnamed staff') + ' | Status: ' + _text(row.get('status')))
        lines.append('')
    if cancelled:
        lines.extend([f'Cancelled records excluded: {cancelled}', ''])
    lines.extend(['Source: today\'s recorded bot attendance sessions.',
                  'Open attendance does not prove physical presence. No check-in does not prove absence.',
                  'Elapsed time includes time away; it is not a payroll calculation.'])
    return {'ok': True, 'report': 'who_in_office', 'report_date': today.isoformat(),
            'timezone': 'Asia/Kolkata', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'counts': {'working': len(working), 'checked_out': len(checked_out),
                       'needs_verification': len(uncertain), 'cancelled': cancelled},
            'warnings': warnings, 'summary': '\n'.join(lines)}
