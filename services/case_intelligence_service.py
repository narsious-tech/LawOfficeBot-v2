"""Sprint 15.0.1 case intelligence and 5:00 PM physical-file update list."""
from __future__ import annotations

import os
from datetime import date

import psycopg2


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def todays_next_dates():
    """Return next dates recorded today, newest completion per case.

    The next purpose is taken from the same authoritative hearing-timeline
    entry as the next date, ensuring Jimmy receives a consistent file update.
    """
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),'')))
                   COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),''), 'Case') AS case_number,
                   COALESCE(NULLIF(TRIM(c.case_title),''), NULLIF(TRIM(lh.case_title),''), 'Untitled case') AS case_title,
                   t.next_hearing_date,
                   COALESCE(NULLIF(TRIM(t.next_purpose),''), NULLIF(TRIM(c.next_purpose),''), 'Purpose not entered') AS next_purpose
            FROM case_hearing_timeline t
            LEFT JOIN cases c ON c.id=t.case_record_id
            LEFT JOIN live_hearings lh ON lh.id=t.live_hearing_id
            WHERE t.next_hearing_date IS NOT NULL
              AND t.created_at::date = CURRENT_DATE
            ORDER BY COALESCE(NULLIF(TRIM(c.case_number),''), NULLIF(TRIM(t.case_number),'')), t.created_at DESC
        """)
        return [
            {
                "case_number": row[0],
                "case_title": row[1],
                "next_date": row[2],
                "next_purpose": row[3],
            }
            for row in cur.fetchall()
        ]


def render_next_dates(rows):
    lines = [
        "📁 <b>PHYSICAL FILE UPDATE</b>",
        "🕔 5:00 PM",
        f"📅 {date.today().strftime('%d %b %Y')}",
        "",
        "Jimmy, please update the following physical files:",
        "",
    ]
    if not rows:
        lines.append("No next dates were recorded today.")
        return "\n".join(lines)

    for index, row in enumerate(rows, 1):
        next_date = (
            row["next_date"].strftime("%d %b %Y")
            if hasattr(row["next_date"], "strftime")
            else str(row["next_date"])
        )
        purpose = str(row.get("next_purpose") or "Purpose not entered").strip()
        lines.append(f"{index}. <b>{row['case_title']}</b>")
        lines.append(f"   {row['case_number']}")
        lines.append(f"   📅 Next Date: <b>{next_date}</b>")
        lines.append(f"   🎯 Next Purpose: <b>{purpose}</b>")
        lines.append("")

    lines.append(f"<b>Total Cases: {len(rows)}</b>")
    return "\n".join(lines)


def jimmy_telegram_id():
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT telegram_user_id FROM staff_accounts
            WHERE is_active=TRUE AND telegram_user_id IS NOT NULL
              AND LOWER(TRIM(staff_name))='jimmy'
            LIMIT 1
        """)
        row = cur.fetchone()
        return row[0] if row else None
