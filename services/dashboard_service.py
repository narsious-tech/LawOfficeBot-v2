"""Live dashboard summary service for LawOfficeBot v3 Sprint 1B.

The service is intentionally read-only. Every source is isolated so one failed
integration does not prevent /start, /home or the Dashboard button from working.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg2
from bs4 import BeautifulSoup

from advocate_web import AdvocateWeb
from config import DATABASE_URL

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CACHE_SECONDS = 60


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    hearings_today: int | None = None
    appointments_today: int | None = None
    pending_works: int | None = None
    pending_tasks: int | None = None
    staff_present: int | None = None
    staff_total: int | None = None
    documents_today: int | None = None
    notifications: int | None = None


_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, DashboardSummary]] = {}


def _cache_key(telegram_user_id: int | None) -> str:
    return str(telegram_user_id or "anonymous")


def _cached_summary(key: str) -> DashboardSummary | None:
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        saved_at, summary = item
        if time.monotonic() - saved_at > CACHE_SECONDS:
            _cache.pop(key, None)
            return None
        return summary


def _save_summary(key: str, summary: DashboardSummary) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), summary)


def clear_dashboard_cache() -> None:
    """Allow future write commands to force an immediate dashboard refresh."""
    with _cache_lock:
        _cache.clear()


def _safe_count(name: str, loader: Callable[[], int | None]) -> int | None:
    try:
        return loader()
    except Exception:
        logger.exception("Dashboard source failed: %s", name)
        return None


def _connect():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        application_name="law-office-bot-v3-dashboard",
    )


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    row = cursor.fetchone()
    return bool(row and row[0])


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        )
        """,
        (table_name, column_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value

    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None

    # Keep the date portion when a time follows it.
    candidates = [text, text.split(" ")[0]]
    formats = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%d %b %Y",
        "%d %B %Y",
    )
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    return None


def _count_hearings_today() -> int:
    today = datetime.now(IST).date()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "cases"):
                return 0

            date_columns = [
                name
                for name in ("next_hearing", "hearing_date")
                if _column_exists(cur, "cases", name)
            ]
            if not date_columns:
                return 0

            select_list = ", ".join(date_columns)
            cur.execute(f"SELECT {select_list} FROM cases")
            count = 0
            for row in cur.fetchall():
                if any(_parse_date(value) == today for value in row):
                    count += 1
            return count
    finally:
        conn.close()


def _count_pending_tasks() -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "tasks"):
                return 0
            cur.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE COALESCE(UPPER(TRIM(status)), 'PENDING')
                      NOT IN ('COMPLETED', 'COMPLETE', 'DONE', 'CLOSED', 'CANCELLED')
                """
            )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _attendance_counts() -> tuple[int, int]:
    today = datetime.now(IST).date()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            present = 0
            total = 0

            if _table_exists(cur, "staff_accounts"):
                cur.execute(
                    "SELECT COUNT(*) FROM staff_accounts WHERE COALESCE(is_active, TRUE) = TRUE"
                )
                total = int(cur.fetchone()[0])
            elif _table_exists(cur, "staff"):
                cur.execute("SELECT COUNT(*) FROM staff")
                total = int(cur.fetchone()[0])

            if _table_exists(cur, "attendance_sessions"):
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT telegram_user_id)
                    FROM attendance_sessions
                    WHERE attendance_date = %s
                      AND checkin_time IS NOT NULL
                      AND checkout_time IS NULL
                      AND COALESCE(UPPER(status), 'OPEN') <> 'CANCELLED'
                    """,
                    (today,),
                )
                present = int(cur.fetchone()[0])

            return present, total
    finally:
        conn.close()


def _count_documents_today() -> int:
    today = datetime.now(IST).date()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "case_files"):
                return 0
            cur.execute(
                "SELECT COUNT(*) FROM case_files WHERE uploaded_at::date = %s",
                (today,),
            )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _count_notifications() -> int:
    """Count actionable office alerts without creating a new notification table."""
    now = datetime.now(IST)
    today = now.date()
    total = 0
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if _table_exists(cur, "attendance_notifications"):
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM attendance_notifications
                    WHERE COALESCE(UPPER(TRIM(approval_status)), '')
                          NOT IN ('APPROVED', 'ACCEPTED')
                    """
                )
                total += int(cur.fetchone()[0])

            if _table_exists(cur, "tasks"):
                columns = {
                    name: _column_exists(cur, "tasks", name)
                    for name in ("deadline", "due_at")
                }
                selected = [name for name, exists in columns.items() if exists]
                if selected:
                    cur.execute(
                        f"""
                        SELECT {', '.join(selected)}
                        FROM tasks
                        WHERE COALESCE(UPPER(TRIM(status)), 'PENDING')
                              NOT IN ('COMPLETED', 'COMPLETE', 'DONE', 'CLOSED', 'CANCELLED')
                        """
                    )
                    for row in cur.fetchall():
                        due_dates = [_parse_date(value) for value in row]
                        if any(due is not None and due < today for due in due_dates):
                            total += 1
            return total
    finally:
        conn.close()


def _count_pending_works() -> int:
    response = AdvocateWeb().works("pending")
    if response.status_code != 200:
        raise RuntimeError(f"Advocate Diaries works returned HTTP {response.status_code}")

    soup = BeautifulSoup(response.text, "lxml")
    tbody = soup.find("tbody")
    if tbody is None:
        return 0

    count = 0
    for row in tbody.find_all("tr"):
        if len(row.find_all("td")) >= 3:
            count += 1
    return count


def get_dashboard_summary(telegram_user_id: int | None = None) -> DashboardSummary:
    """Return live dashboard metrics with a short cache and graceful fallback."""
    key = _cache_key(telegram_user_id)
    cached = _cached_summary(key)
    if cached is not None:
        return cached

    present, total = (None, None)
    try:
        present, total = _attendance_counts()
    except Exception:
        logger.exception("Dashboard source failed: attendance")

    summary = DashboardSummary(
        hearings_today=_safe_count("hearings_today", _count_hearings_today),
        # Appointments remain deliberately unavailable until the Advocate
        # Diaries appointments endpoint is integrated in Sprint 3.
        appointments_today=None,
        pending_works=_safe_count("pending_works", _count_pending_works),
        pending_tasks=_safe_count("pending_tasks", _count_pending_tasks),
        staff_present=present,
        staff_total=total,
        documents_today=_safe_count("documents_today", _count_documents_today),
        notifications=_safe_count("notifications", _count_notifications),
    )
    _save_summary(key, summary)
    return summary


def greeting_for_now(now: datetime | None = None) -> str:
    current = now.astimezone(IST) if now else datetime.now(IST)
    if current.hour < 12:
        return "Good Morning"
    if current.hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def display_count(value: int | None) -> str:
    return "--" if value is None else str(value)


def display_attendance(present: int | None, total: int | None) -> str:
    if present is None or total is None:
        return "--"
    return f"{present}/{total}"
