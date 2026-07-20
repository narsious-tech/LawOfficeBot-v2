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
    """Parse the authenticated, server-rendered Advocate Diaries Pending Cases page.

    Only rows inside ``table#cases-list > tbody`` are accepted.  The generic
    court-case API is deliberately never used because ``status=pending`` means
    an active case, not a missing next-hearing update.
    """
    import re
    from bs4 import BeautifulSoup
    from advocate_web import AdvocateWeb

    web = AdvocateWeb()
    response = web.get("/pendingCases")
    response.raise_for_status()

    final_url = str(getattr(response, "url", "") or "")
    if final_url and "/pendingCases" not in final_url:
        raise RuntimeError(f"Advocate Diaries redirected pendingCases to {final_url}")

    soup = BeautifulSoup(response.text, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    table = soup.select_one("table#cases-list")
    if "Pending Cases" not in title or table is None:
        raise RuntimeError("Authenticated Pending Cases table was not found")

    rows = table.select("tbody > tr[id^='case_']")
    pending: list[dict[str, Any]] = []

    def labelled_value(node, label: str) -> str:
        text = " ".join(node.get_text(" ", strip=True).split())
        match = re.search(rf"{re.escape(label)}\s*(.*)", text, flags=re.I)
        return match.group(1).strip() if match else ""

    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            continue

        case_id = (row.get("id") or "").removeprefix("case_").strip()
        case_cell, court_cell, hearing_cell = cells[1], cells[2], cells[3]

        # Read only the immediate field rows inside each wrapper.  Using
        # ``select("div")`` recursively caused the wrapper text to absorb Case
        # Type, Case Number and status into the case title.
        case_wrapper = case_cell.select_one(".table_col_wrapper") or case_cell
        case_lines = [
            " ".join(x.get_text(" ", strip=True).split())
            for x in case_wrapper.find_all("div", recursive=False)
        ]
        title_line = next((x for x in case_lines if x.lower().startswith("case title:")), "")
        number_line = next((x for x in case_lines if x.lower().startswith("case number:")), "")
        type_line = next((x for x in case_lines if x.lower().startswith("case type:")), "")

        court_wrapper = court_cell.select_one(".table_col_wrapper") or court_cell
        court_lines = [
            " ".join(x.get_text(" ", strip=True).split())
            for x in court_wrapper.find_all("div", recursive=False)
        ]
        court_line = next((x for x in court_lines if x.lower().startswith("court:")), "")
        judge_line = next((x for x in court_lines if x.lower().startswith("judge:")), "")

        hearing_wrapper = hearing_cell.select_one(".table_col_wrapper") or hearing_cell
        next_hearing_node = hearing_wrapper.select_one("span.blinking")
        next_hearing = next_hearing_node.get_text(" ", strip=True) if next_hearing_node else ""
        hearing_lines = [
            " ".join(x.get_text(" ", strip=True).split())
            for x in hearing_wrapper.find_all("div", recursive=False)
        ]
        purpose_line = next((x for x in hearing_lines if x.lower().startswith("purpose:")), "")
        previous_line = next((x for x in hearing_lines if x.lower().startswith("previous hearing:")), "")

        title_value = title_line.split(":", 1)[1].strip() if ":" in title_line else "Untitled case"
        number_value = number_line.split(":", 1)[1].strip() if ":" in number_line else ""
        purpose_value = purpose_line.split(":", 1)[1].strip() if ":" in purpose_line else ""

        pending.append({
            "advocate_diaries_id": case_id,
            "case_number": number_value or "Case number not entered",
            "case_title": title_value or "Untitled case",
            "case_type": type_line.split(":", 1)[1].strip() if ":" in type_line else "",
            "court": court_line.split(":", 1)[1].strip() if ":" in court_line else "",
            "judge": judge_line.split(":", 1)[1].strip() if ":" in judge_line else "",
            "next_date": next_hearing or None,
            "next_purpose": purpose_value or None,
            "previous_hearing": previous_line.split(":", 1)[1].strip() if ":" in previous_line else "",
            # Every row is pending because Advocate Diaries itself placed it on
            # the dedicated "Whom Next Hearing date not updated" page.
            "missing": ["Advocate Diaries next-hearing update pending"],
        })

    print(
        "Sprint 16.0.2 pending parser: "
        f"url={final_url or '/pendingCases'} title={title!r} rows={len(rows)} parsed={len(pending)}"
    )
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
    lines.append(f"✅ <b>NEXT DATES RECORDED TODAY — {len(rows)}</b>")
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
        lines.append(f"   Previous: <b>{html.escape(_fmt_date(row.get('previous_hearing')))}</b>")
        lines.append(f"   Purpose: <b>{html.escape(str(row.get('next_purpose') or 'Not entered'))}</b>")
        lines.append("   Pending: <b>Next hearing date update</b>")
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
        f"<b>Summary: {len(updated)} next dates recorded today · {len(pending)} pending</b>"
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
