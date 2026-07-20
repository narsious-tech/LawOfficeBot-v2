"""Sprint 15.0.3 physical-file and pending-hearing update intelligence."""
from __future__ import annotations

import html
import os
from datetime import date
from typing import Any

import psycopg2
import requests


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def todays_next_dates() -> list[dict[str, Any]]:
    """Return the latest next-date/purpose entry created today for each case."""
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
            {"case_number": r[0], "case_title": r[1], "next_date": r[2], "next_purpose": r[3]}
            for r in cur.fetchall()
        ]


def _ad_token() -> str | None:
    api = (os.getenv("AD_API") or "").rstrip("/")
    email = os.getenv("AD_EMAIL")
    password = os.getenv("AD_PASSWORD")
    if not api or not email or not password:
        return None
    response = requests.post(
        f"{api}/login", json={"email": email, "password": password}, timeout=25
    )
    response.raise_for_status()
    payload = response.json()
    return ((payload.get("data") or {}).get("access_token"))


def advocate_diaries_pending_cases() -> list[dict[str, Any]]:
    """Read only the dedicated Advocate Diaries Pending Cases page.

    This intentionally does not treat every court case whose generic status is
    'pending' as a missing hearing update.
    """
    from bs4 import BeautifulSoup
    from advocate_web import AdvocateWeb
    web = AdvocateWeb()
    response = web.get("/pendingCases")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    pending: list[dict[str, Any]] = []
    # The page is rendered as one record per table row/card.
    candidates = soup.select("table tbody tr") or soup.select(".pending-case, .case-row, .card")
    for node in candidates:
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text or "Case Title:" not in text:
            continue
        def grab(label: str, stops: tuple[str, ...]) -> str:
            import re
            stop = "|".join(re.escape(x) for x in stops)
            m = re.search(re.escape(label) + r"\s*(.*?)(?=" + stop + r"|$)", text, flags=re.I)
            return (m.group(1).strip() if m else "")
        title = grab("Case Title:", ("Case Type:", "Case Number:", "Court:")) or "Untitled case"
        number = grab("Case Number:", ("Pending", "Court:", "Judge:", "Next Hearing:")) or "Case"
        next_date = grab("Next Hearing:", ("Purpose:", "Previous Hearing:", "Settlement"))
        purpose = grab("Purpose:", ("Previous Hearing:", "Settlement", "Paid Amount:"))
        missing = []
        if not next_date: missing.append("Next date")
        if not purpose: missing.append("Next purpose")
        pending.append({"case_number": number, "case_title": title, "next_date": next_date or None,
                        "next_purpose": purpose or None,
                        "missing": missing or ["Advocate Diaries update pending"]})
    return pending


def owner_for_case(case_number: str) -> str:
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT owner_staff FROM case_ownership
            WHERE LOWER(TRIM(case_number))=LOWER(TRIM(%s)) AND active=TRUE
            ORDER BY updated_at DESC, id DESC LIMIT 1
        """, (case_number,))
        row = cur.fetchone()
        return str(row[0]).strip() if row and row[0] else "Preet"


def pending_grouped_by_owner(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        owner = owner_for_case(row["case_number"])
        row = dict(row)
        row["owner"] = owner
        grouped.setdefault(owner, []).append(row)
    return grouped


def staff_telegram_id(staff_name: str) -> int | None:
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT telegram_user_id FROM staff_accounts
            WHERE is_active=TRUE AND telegram_user_id IS NOT NULL
              AND LOWER(TRIM(staff_name))=LOWER(TRIM(%s))
            LIMIT 1
        """, (staff_name,))
        row = cur.fetchone()
        return int(row[0]) if row else None


def jimmy_telegram_id() -> int | None:
    return staff_telegram_id("Jimmy")


def _fmt_date(value: Any) -> str:
    if not value:
        return "Not entered"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    return str(value)


def render_updated_cases(rows: list[dict[str, Any]], *, include_header: bool = True) -> str:
    lines = []
    if include_header:
        lines.extend([
            "📁 <b>PHYSICAL FILE UPDATE</b>",
            "🕔 5:00 PM",
            f"📅 {date.today().strftime('%d %b %Y')}", "",
            "Jimmy, please update the following physical files:", "",
        ])
    lines.append(f"✅ <b>UPDATED CASES — {len(rows)}</b>")
    lines.append("")
    if not rows:
        lines.append("No next-date or purpose updates were recorded today.")
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. <b>{html.escape(str(row['case_title']))}</b>")
        lines.append(f"   {html.escape(str(row['case_number']))}")
        lines.append(
            f"   📅 <b>{html.escape(_fmt_date(row.get('next_date')))}</b> | "
            f"🎯 <b>{html.escape(str(row.get('next_purpose') or 'Purpose not entered'))}</b>"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def render_pending_cases(rows: list[dict[str, Any]], *, heading: str = "PENDING CASES") -> str:
    lines = [f"⚠️ <b>{html.escape(heading)} — {len(rows)}</b>", ""]
    if not rows:
        lines.append("No Advocate Diaries cases are pending update.")
    for i, row in enumerate(rows, 1):
        missing = ", ".join(row.get("missing") or ["Update pending"])
        lines.append(f"{i}. <b>{html.escape(str(row['case_title']))}</b>")
        lines.append(f"   {html.escape(str(row['case_number']))}")
        lines.append(f"   Missing: <b>{html.escape(missing)}</b>")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_office_report(updated: list[dict[str, Any]], pending: list[dict[str, Any]]) -> str:
    return (
        "📋 <b>DAILY HEARING & PHYSICAL FILE REPORT</b>\n"
        "🕔 5:00 PM\n"
        f"📅 {date.today().strftime('%d %b %Y')}\n\n"
        f"{render_updated_cases(updated, include_header=False)}\n\n"
        "────────────────────\n\n"
        f"{render_pending_cases(pending)}\n\n"
        f"<b>Summary: {len(updated)} updated · {len(pending)} pending</b>"
    )


def split_html_message(text: str, limit: int = 3900) -> list[str]:
    """Split at line boundaries; generated markup is line-local and remains valid."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
