"""Read-only chronological case timeline for LawOfficeBot v3 Sprint 4.

The service prefers authoritative entries from ``client_timeline`` (the existing
activity logger), then supplements them with safe read-only legacy facts so a
case still has a useful history even when older actions pre-date activity
logging.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import html
from typing import Any, Iterable, Optional

from psycopg2.extras import RealDictCursor

from services.case_workspace_service import CaseSummary, _connect


@dataclass(frozen=True)
class TimelineEvent:
    event_at: datetime
    title: str
    details: str
    category: str
    status: str
    actor: str
    source_type: str
    source_id: str


def _clean(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _identifiers(case: CaseSummary) -> list[str]:
    values = [case.case_number, case.case_id, case.ad_case_id]
    return [v for v in values if v and v != "-"] or ["__no_case_identifier__"]


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    row = cur.fetchone()
    return bool(row and row[0])


def _columns(cur, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {str(row[0]) for row in cur.fetchall()}


def _actor_name(cur, user_id: Any) -> str:
    if not user_id:
        return "System"
    try:
        cur.execute(
            """
            SELECT staff_name
            FROM staff_accounts
            WHERE telegram_user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return f"User {user_id}"


def _logged_events(cur, case: CaseSummary, limit: int) -> list[TimelineEvent]:
    if not _table_exists(cur, "client_timeline"):
        return []
    ids = _identifiers(case)
    cur.execute(
        """
        SELECT event_at, created_at, event_title, event_details,
               event_category, event_status, created_by,
               source_type, source_id
        FROM client_timeline
        WHERE case_number = ANY(%s) OR case_id = ANY(%s)
        ORDER BY COALESCE(event_at, created_at) DESC, id DESC
        LIMIT %s
        """,
        (ids, ids, limit),
    )
    events: list[TimelineEvent] = []
    for row in cur.fetchall():
        when = _dt(row[0]) or _dt(row[1]) or datetime.now()
        events.append(
            TimelineEvent(
                event_at=when,
                title=_clean(row[2], "Activity"),
                details=_clean(row[3], ""),
                category=_clean(row[4], "other").lower(),
                status=_clean(row[5], "RECORDED"),
                actor=_actor_name(cur, row[6]),
                source_type=_clean(row[7], "SYSTEM"),
                source_id=_clean(row[8], ""),
            )
        )
    return events


def _legacy_task_events(cur, case: CaseSummary) -> list[TimelineEvent]:
    if not _table_exists(cur, "tasks"):
        return []
    cols = _columns(cur, "tasks")
    ids = _identifiers(case)
    time_col = next((c for c in ("completed_at", "updated_at", "created_at", "due_at") if c in cols), None)
    select_time = time_col if time_col else "NULL"
    cur.execute(
        f"""
        SELECT id, task, assigned_to, status, deadline, {select_time}
        FROM tasks
        WHERE case_number = ANY(%s)
        ORDER BY id DESC
        LIMIT 100
        """,
        (ids,),
    )
    events: list[TimelineEvent] = []
    for row in cur.fetchall():
        task_id, task, assigned_to, status, deadline, raw_when = row
        when = _dt(raw_when) or _dt(deadline)
        if not when:
            continue
        completed = str(status or "").upper() == "COMPLETED"
        events.append(
            TimelineEvent(
                event_at=when,
                title="Task completed" if completed else "Task recorded",
                details=f"Task #{task_id}: {_clean(task)}\nAssigned to: {_clean(assigned_to)}",
                category="tasks",
                status=_clean(status, "PENDING"),
                actor=_clean(assigned_to, "System"),
                source_type="TASK_LEGACY",
                source_id=str(task_id),
            )
        )
    return events


def _legacy_fee_events(cur, case: CaseSummary) -> list[TimelineEvent]:
    if not _table_exists(cur, "fee_installments"):
        return []
    ids = _identifiers(case)
    cur.execute(
        """
        SELECT id, amount, date
        FROM fee_installments
        WHERE case_number = ANY(%s)
        ORDER BY id DESC
        LIMIT 100
        """,
        (ids,),
    )
    events: list[TimelineEvent] = []
    for fee_id, amount, raw_date in cur.fetchall():
        when = _dt(raw_date)
        if not when:
            continue
        events.append(
            TimelineEvent(
                event_at=when,
                title="Fee installment recorded",
                details=f"Amount: {_clean(amount)}",
                category="fees",
                status="RECORDED",
                actor="System",
                source_type="FEE_LEGACY",
                source_id=str(fee_id),
            )
        )
    return events


def _case_fact_events(cur, case: CaseSummary) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    cols = _columns(cur, "cases") if _table_exists(cur, "cases") else set()
    if cols:
        created_col = next((c for c in ("created_at", "created_on", "date_created") if c in cols), None)
        if created_col:
            cur.execute(f"SELECT {created_col} FROM cases WHERE id = %s", (case.db_id,))
            row = cur.fetchone()
            when = _dt(row[0]) if row else None
            if when:
                events.append(
                    TimelineEvent(
                        event_at=when,
                        title="Case created",
                        details=f"{case.case_number}\n{case.case_title}",
                        category="case",
                        status=case.status,
                        actor="System",
                        source_type="CASE",
                        source_id=str(case.db_id),
                    )
                )
    hearing = _dt(case.next_hearing)
    if hearing:
        events.append(
            TimelineEvent(
                event_at=hearing,
                title="Scheduled hearing",
                details=f"Court: {case.court_name}\nJudge: {case.judge_name}",
                category="hearing",
                status="SCHEDULED",
                actor="Advocate Diaries",
                source_type="HEARING_SNAPSHOT",
                source_id=f"{case.db_id}:{hearing.date().isoformat()}",
            )
        )
    return events


def _dedupe(events: Iterable[TimelineEvent]) -> list[TimelineEvent]:
    seen: set[tuple[str, str, str]] = set()
    result: list[TimelineEvent] = []
    for event in sorted(events, key=lambda e: e.event_at, reverse=True):
        key = (
            event.source_type.lower(),
            event.source_id.lower(),
            event.title.lower(),
        )
        if event.source_id and key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def get_case_timeline(case: CaseSummary, limit: int = 40) -> list[TimelineEvent]:
    """Return a safe, read-only, newest-first timeline for one case."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            logged = _logged_events(cur, case, limit * 2)
            legacy = _legacy_task_events(cur, case)
            legacy += _legacy_fee_events(cur, case)
            legacy += _case_fact_events(cur, case)
        return _dedupe([*logged, *legacy])[:limit]
    finally:
        conn.close()


def _icon(category: str, title: str) -> str:
    value = f"{category} {title}".lower()
    if "hearing" in value:
        return "⚖️"
    if "document" in value or "drive" in value:
        return "📂"
    if "fee" in value or "payment" in value:
        return "💰"
    if "work" in value:
        return "📋"
    if "task" in value:
        return "✅"
    if "note" in value:
        return "📝"
    if "staff" in value or "assign" in value:
        return "👤"
    if "case" in value:
        return "📌"
    return "•"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def render_timeline(case: CaseSummary, events: list[TimelineEvent]) -> str:
    identifier = case.case_number if case.case_number != "-" else case.case_id
    lines = [
        "📜 <b>CASE TIMELINE</b>",
        f"🆔 <b>{_esc(identifier)}</b>",
        f"📌 {_esc(case.case_title)}",
        "",
    ]
    if not events:
        lines.append("No timeline activity has been recorded for this case yet.")
        return "\n".join(lines)

    current_day: Optional[date] = None
    for event in events:
        day = event.event_at.date()
        if day != current_day:
            if current_day is not None:
                lines.append("")
            lines.append(f"<b>{event.event_at.strftime('%d %b %Y')}</b>")
            current_day = day
        icon = _icon(event.category, event.title)
        lines.append(f"{icon} <b>{_esc(event.title)}</b> · {event.event_at.strftime('%I:%M %p')}")
        if event.details and event.details != "-":
            detail = _esc(event.details).replace("\n", "\n   ")
            lines.append(f"   {detail}")
        lines.append(f"   👤 {_esc(event.actor)} · {_esc(event.status)}")
    return "\n".join(lines)
