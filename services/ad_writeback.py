from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote

import psycopg2
from bs4 import BeautifulSoup

from advocate_web import AdvocateWeb, BASE_URL
from config import DATABASE_URL


@dataclass
class WritebackResult:
    status: str
    message: str
    remote_case_id: str | None = None
    endpoint: str | None = None
    verified: bool = False


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-ad-writeback")


def ensure_sync_queue() -> None:
    conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS advocate_diaries_sync_queue (
            id SERIAL PRIMARY KEY,
            live_hearing_id INTEGER,
            case_number TEXT NOT NULL,
            remote_case_id TEXT,
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_sync_queue_status ON advocate_diaries_sync_queue(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_sync_queue_case ON advocate_diaries_sync_queue(case_number)")
        conn.commit()
    finally:
        cur.close(); conn.close()


def queue_writeback(live_hearing_id: int, case_number: str, payload: dict[str, Any], error: str, remote_case_id: str | None = None) -> int:
    ensure_sync_queue(); conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
        INSERT INTO advocate_diaries_sync_queue(live_hearing_id,case_number,remote_case_id,payload,status,attempts,last_error,next_retry_at)
        VALUES (%s,%s,%s,%s::jsonb,'PENDING',1,%s,CURRENT_TIMESTAMP + INTERVAL '15 minutes')
        RETURNING id
        """, (live_hearing_id, case_number, remote_case_id, json.dumps(payload), error[:2000]))
        queue_id = cur.fetchone()[0]; conn.commit(); return queue_id
    finally:
        cur.close(); conn.close()


def _norm_case_number(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _extract_csrf(web: AdvocateWeb, html: str = "") -> str:
    """Extract CakePHP CSRF request token from page markup or the authenticated cookie jar."""
    soup = BeautifulSoup(html or "", "lxml")
    selectors = [
        ("meta", {"name": "csrfToken"}, "content"),
        ("meta", {"name": "csrf-token"}, "content"),
        ("input", {"name": "_csrfToken"}, "value"),
    ]
    for tag, attrs, attr in selectors:
        node = soup.find(tag, attrs)
        if node and node.get(attr):
            return str(node.get(attr)).strip()

    for pattern in (
        r'csrfToken\s*[:=]\s*["\']([^"\']+)',
        r'_csrfToken\s*[:=]\s*["\']([^"\']+)',
        r'X-CSRF-Token["\']?\s*[:=]\s*["\']([^"\']+)',
    ):
        match = re.search(pattern, html or "", flags=re.I)
        if match:
            return match.group(1).strip()

    cookie = web.session.cookies.get("csrfToken")
    if cookie:
        return unquote(cookie)
    raise RuntimeError("Authenticated Advocate Diaries CSRF token was not found")


def _parse_day_cases(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    records: list[dict[str, str]] = []
    for row in soup.select("tr[id^='case_']"):
        remote_id = row.get("id", "").removeprefix("case_").strip()
        link = row.select_one(".add-next-hearing-dashboard")
        if link and link.get("data-id"):
            remote_id = str(link.get("data-id")).strip()
        text = row.get_text("\n", strip=True)
        number_match = re.search(r"Case Number:\s*([^\n]+)", text, flags=re.I)
        purpose_match = re.search(r"Purpose:\s*([^\n]+)", text, flags=re.I)
        previous_match = re.search(r"Previous Hearing:\s*([^\n]+)", text, flags=re.I)
        records.append({
            "remote_id": remote_id,
            "case_number": number_match.group(1).strip() if number_match else "",
            "purpose": (link.get("data-purpose") if link else None) or (purpose_match.group(1).strip() if purpose_match else ""),
            "previous_hearing_date": (link.get("data-previous-hearing-date") if link else None) or (previous_match.group(1).strip() if previous_match else ""),
        })
    return records


def _load_day_page(web: AdvocateWeb, case_date: date) -> tuple[str, str]:
    web.ensure_login()
    endpoint = f"{BASE_URL}/dashboard/search-day-cases"
    response = web.session.get(
        endpoint,
        params={"case_date": case_date.isoformat()},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/dashboard?currentView=dashboard",
            "Accept": "*/*",
        },
        timeout=(10, 90),
    )
    response.raise_for_status()
    if "/auth/login" in response.url:
        raise RuntimeError("Advocate Diaries web session expired during cause-list lookup")
    return response.text, _extract_csrf(web, response.text)


def find_remote_case(web: AdvocateWeb, case_number: str, case_date: date) -> tuple[str | None, dict[str, str] | None, str]:
    html, csrf = _load_day_page(web, case_date)
    wanted = _norm_case_number(case_number)
    for record in _parse_day_cases(html):
        if _norm_case_number(record.get("case_number")) == wanted:
            return record.get("remote_id") or None, record, csrf
    return None, None, csrf


def _payload(
    *, remote_id: str, current_case_date: date, previous_hearing_date: str,
    next_date: date, next_purpose: str, order_summary: str, documents_required: str,
) -> dict[str, str]:
    return {
        "id": remote_id,
        "purpose": (next_purpose or "").strip(),
        "previous_hearing_date": (previous_hearing_date or f"{current_case_date.month}/{current_case_date.day}/{str(current_case_date.year)[2:]}").strip(),
        "next_hearing_date": next_date.isoformat(),
        "remarks": (order_summary or "").strip(),
        "case_date": current_case_date.isoformat(),
        "work": (documents_required or "").strip(),
    }


def _verify(web: AdvocateWeb, case_number: str, expected_date: date) -> bool:
    """Verification succeeds when the case appears on the newly submitted hearing date."""
    try:
        html, _ = _load_day_page(web, expected_date)
        wanted = _norm_case_number(case_number)
        return any(_norm_case_number(r.get("case_number")) == wanted for r in _parse_day_cases(html))
    except Exception:
        return False


def _post(web: AdvocateWeb, payload: dict[str, str], csrf: str):
    endpoint = f"{BASE_URL}/hearings/add-dashboard-hearing"
    response = web.session.post(
        endpoint,
        data=payload,
        headers={
            "X-CSRF-Token": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/dashboard?currentView=dashboard",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        timeout=(10, 90),
        allow_redirects=True,
    )
    if "/auth/login" in response.url:
        raise RuntimeError("Advocate Diaries web session expired during hearing update")
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:800]}")
    low = response.text.casefold()
    if any(marker in low for marker in ("error", "invalid", "failed", "unauthorized")) and "success" not in low:
        raise RuntimeError(f"Advocate Diaries rejected update: {response.text[:800]}")
    return response


def writeback_hearing(
    *, live_hearing_id: int, case_number: str, hearing_date: date,
    next_date: date | None, next_purpose: str, order_summary: str, documents_required: str,
    remote_case_id: str | None = None, previous_hearing_date: str | None = None,
) -> WritebackResult:
    ensure_sync_queue()
    if next_date is None:
        return WritebackResult("SKIPPED", "Disposed matter: no next-hearing date was submitted to Advocate Diaries.")

    web = AdvocateWeb()
    provisional = {
        "case_number": case_number,
        "case_date": hearing_date.isoformat(),
        "next_hearing_date": next_date.isoformat(),
        "purpose": next_purpose,
        "remarks": order_summary,
        "work": documents_required,
    }
    try:
        found_id, record, csrf = find_remote_case(web, case_number, hearing_date)
        remote_id = remote_case_id or found_id
        if not remote_id:
            raise RuntimeError(f"Matching Advocate Diaries case UUID not found on {hearing_date.isoformat()}")
        payload = _payload(
            remote_id=remote_id,
            current_case_date=hearing_date,
            previous_hearing_date=previous_hearing_date or (record or {}).get("previous_hearing_date", ""),
            next_date=next_date,
            next_purpose=next_purpose,
            order_summary=order_summary,
            documents_required=documents_required,
        )
        _post(web, payload, csrf)
        verified = _verify(web, case_number, next_date)
        message = "Advocate Diaries hearing updated and verified." if verified else "Advocate Diaries accepted the hearing update; verification is pending."
        return WritebackResult("SUCCESS", message, remote_case_id=remote_id,
                               endpoint=f"{BASE_URL}/hearings/add-dashboard-hearing", verified=verified)
    except Exception as exc:
        qid = queue_writeback(live_hearing_id, case_number, provisional, f"{type(exc).__name__}: {exc}", remote_case_id)
        return WritebackResult("QUEUED", f"Advocate Diaries sync queued as #{qid}: {type(exc).__name__}: {exc}", remote_case_id=remote_case_id)


def retry_pending(limit: int = 20) -> dict[str, int]:
    ensure_sync_queue(); conn = _connect(); cur = conn.cursor()
    stats = {"processed": 0, "success": 0, "failed": 0}
    try:
        cur.execute("""
        SELECT id,live_hearing_id,case_number,remote_case_id,payload
        FROM advocate_diaries_sync_queue
        WHERE status='PENDING' AND (next_retry_at IS NULL OR next_retry_at<=CURRENT_TIMESTAMP)
        ORDER BY id LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        for queue_id, live_id, case_number, remote_id, payload in rows:
            stats["processed"] += 1
            try:
                case_date = date.fromisoformat(payload["case_date"])
                next_date = date.fromisoformat(payload["next_hearing_date"])
                result = writeback_hearing(
                    live_hearing_id=live_id,
                    case_number=case_number,
                    remote_case_id=remote_id,
                    hearing_date=case_date,
                    next_date=next_date,
                    next_purpose=payload.get("purpose", ""),
                    order_summary=payload.get("remarks", ""),
                    documents_required=payload.get("work", ""),
                )
                if result.status != "SUCCESS":
                    raise RuntimeError(result.message)
                cur.execute("""
                    UPDATE advocate_diaries_sync_queue
                    SET status='SUCCESS',remote_case_id=%s,completed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP,last_error=NULL
                    WHERE id=%s
                """, (result.remote_case_id, queue_id))
                conn.commit(); stats["success"] += 1
            except Exception as exc:
                cur.execute("""
                    UPDATE advocate_diaries_sync_queue SET attempts=attempts+1,last_error=%s,
                    next_retry_at=CURRENT_TIMESTAMP + INTERVAL '30 minutes',updated_at=CURRENT_TIMESTAMP
                    WHERE id=%s
                """, (f"{type(exc).__name__}: {exc}"[:2000], queue_id))
                conn.commit(); stats["failed"] += 1
        return stats
    finally:
        cur.close(); conn.close()
