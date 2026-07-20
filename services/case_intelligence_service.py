"""Sprint 15 case intelligence and physical-file next-date list."""
from __future__ import annotations
import os
from datetime import date
import psycopg2


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def todays_next_dates():
    """Return next dates recorded today, newest completion per case."""
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),'')))
                   COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),''), 'Case') AS case_number,
                   COALESCE(NULLIF(TRIM(c.case_title),''), NULLIF(TRIM(lh.case_title),''), 'Untitled case') AS case_title,
                   t.next_hearing_date
            FROM case_hearing_timeline t
            LEFT JOIN cases c ON c.id=t.case_record_id
            LEFT JOIN live_hearings lh ON lh.id=t.live_hearing_id
            WHERE t.next_hearing_date IS NOT NULL
              AND t.created_at::date = CURRENT_DATE
            ORDER BY COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),'')), t.created_at DESC
        """)
        return [{"case_number":r[0], "case_title":r[1], "next_date":r[2]} for r in cur.fetchall()]


def render_next_dates(rows):
    lines=["📁 <b>PHYSICAL FILES — NEXT DATES</b>", f"📅 Updated today: {date.today().strftime('%d %b %Y')}", ""]
    if not rows:
        lines.append("No next dates were recorded today.")
        return "\n".join(lines)
    for i,r in enumerate(rows,1):
        d=r['next_date'].strftime('%d %b %Y') if hasattr(r['next_date'],'strftime') else str(r['next_date'])
        lines.append(f"{i}. <b>{r['case_title']}</b>")
        lines.append(f"   {r['case_number']} · <b>{d}</b>")
    lines += ["", "Jimmy: please update these dates on the physical case files."]
    return "\n".join(lines)


def jimmy_telegram_id():
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT telegram_user_id FROM staff_accounts
            WHERE is_active=TRUE AND telegram_user_id IS NOT NULL
              AND LOWER(TRIM(staff_name))='jimmy'
            LIMIT 1
        """)
        row=cur.fetchone()
        return row[0] if row else None
