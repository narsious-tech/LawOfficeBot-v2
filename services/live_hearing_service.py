from __future__ import annotations

import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from commands.dashboard import fetch_advocate_diaries_cause_groups, normalize_space

IST = ZoneInfo("Asia/Kolkata")
ALLOWED_STATUSES = {
    "LISTED", "CALLED", "PASSED_OVER", "ADJOURNED", "ORDER_RESERVED", "DISPOSED"
}


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-live-hearings")


def ensure_live_hearing_tables() -> None:
    conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS live_hearings (
            id SERIAL PRIMARY KEY,
            hearing_key TEXT UNIQUE NOT NULL,
            hearing_date DATE NOT NULL,
            case_number TEXT,
            case_title TEXT,
            stage TEXT,
            judge_name TEXT,
            court_name TEXT,
            floor TEXT,
            room TEXT,
            assigned_to TEXT,
            status TEXT NOT NULL DEFAULT 'LISTED',
            status_note TEXT,
            source TEXT,
            called_at TIMESTAMP,
            completed_at TIMESTAMP,
            updated_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_live_hearings_date ON live_hearings(hearing_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_live_hearings_status ON live_hearings(status)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS live_hearing_events (
            id SERIAL PRIMARY KEY,
            live_hearing_id INTEGER REFERENCES live_hearings(id) ON DELETE CASCADE,
            old_status TEXT,
            new_status TEXT NOT NULL,
            note TEXT,
            changed_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
    finally:
        cur.close(); conn.close()


def _key(target_date: date, group: dict, case_item: dict) -> str:
    raw = "|".join([
        target_date.isoformat(), normalize_space(group.get("court_name")),
        normalize_space(group.get("judge_name")), normalize_space(case_item.get("case_number")),
        normalize_space(case_item.get("case_title")),
    ]).lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sync_live_hearings(target_date: date | None = None) -> tuple[int, str]:
    ensure_live_hearing_tables()
    target_date = target_date or datetime.now(IST).date()
    groups, source = fetch_advocate_diaries_cause_groups(target_date)
    conn = _connect(); cur = conn.cursor()
    count = 0
    try:
        for group in groups:
            for case_item in group.get("cases", []):
                cur.execute("""
                    INSERT INTO live_hearings (
                        hearing_key, hearing_date, case_number, case_title, stage,
                        judge_name, court_name, floor, room, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (hearing_key) DO UPDATE SET
                        case_number=EXCLUDED.case_number, case_title=EXCLUDED.case_title,
                        stage=EXCLUDED.stage, judge_name=EXCLUDED.judge_name,
                        court_name=EXCLUDED.court_name, floor=EXCLUDED.floor,
                        room=EXCLUDED.room, source=EXCLUDED.source, updated_at=CURRENT_TIMESTAMP
                """, (
                    _key(target_date, group, case_item), target_date,
                    normalize_space(case_item.get("case_number")), normalize_space(case_item.get("case_title")),
                    normalize_space(case_item.get("stage")), normalize_space(group.get("judge_name")),
                    normalize_space(group.get("court_name")), normalize_space(group.get("floor")),
                    normalize_space(group.get("room")), source,
                ))
                count += 1
        conn.commit()
        return count, source
    finally:
        cur.close(); conn.close()


def list_live_hearings(target_date: date | None = None):
    ensure_live_hearing_tables(); target_date = target_date or datetime.now(IST).date()
    conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM live_hearings WHERE hearing_date=%s
            ORDER BY COALESCE(NULLIF(floor,''),'999'), COALESCE(NULLIF(room,''),'999'), judge_name, id
        """, (target_date,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def get_live_hearing(hearing_id: int):
    ensure_live_hearing_tables(); conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM live_hearings WHERE id=%s", (hearing_id,))
        row = cur.fetchone(); return dict(row) if row else None
    finally:
        cur.close(); conn.close()


def set_live_hearing_status(hearing_id: int, new_status: str, changed_by: int | None = None):
    new_status = new_status.upper()
    if new_status not in ALLOWED_STATUSES:
        raise ValueError("Unsupported hearing status")
    ensure_live_hearing_tables(); conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT status FROM live_hearings WHERE id=%s FOR UPDATE", (hearing_id,))
        row = cur.fetchone()
        if not row: return None
        old = row["status"]
        called_at = "CURRENT_TIMESTAMP" if new_status == "CALLED" else "called_at"
        completed_at = "CURRENT_TIMESTAMP" if new_status in {"ADJOURNED","ORDER_RESERVED","DISPOSED"} else "completed_at"
        cur.execute(f"""
            UPDATE live_hearings SET status=%s, updated_by=%s, updated_at=CURRENT_TIMESTAMP,
                called_at={called_at}, completed_at={completed_at}
            WHERE id=%s RETURNING *
        """, (new_status, changed_by, hearing_id))
        updated = dict(cur.fetchone())
        cur.execute("""
            INSERT INTO live_hearing_events(live_hearing_id, old_status, new_status, changed_by)
            VALUES (%s,%s,%s,%s)
        """, (hearing_id, old, new_status, changed_by))
        conn.commit(); return updated
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()



def complete_live_hearing(
    hearing_id: int,
    *,
    next_date: date | None,
    next_purpose: str,
    order_summary: str,
    documents_required: str,
    create_task: bool,
    notify_client: bool,
    changed_by: int | None,
):
    """Atomically record a hearing outcome and mirror it into office records."""
    ensure_live_hearing_tables()
    conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hearing_completions (
                id SERIAL PRIMARY KEY,
                live_hearing_id INTEGER UNIQUE REFERENCES live_hearings(id) ON DELETE CASCADE,
                next_date DATE,
                next_purpose TEXT,
                order_summary TEXT,
                documents_required TEXT,
                task_created_id INTEGER,
                notify_client BOOLEAN DEFAULT FALSE,
                completed_by BIGINT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("SELECT * FROM live_hearings WHERE id=%s FOR UPDATE", (hearing_id,))
        hearing = cur.fetchone()
        if not hearing:
            return None
        hearing = dict(hearing)
        final_status = "ADJOURNED" if next_date else "DISPOSED"
        cur.execute("""
            UPDATE live_hearings SET status=%s, status_note=%s, completed_at=CURRENT_TIMESTAMP,
                updated_by=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (final_status, order_summary, changed_by, hearing_id))
        task_id = None
        case_number = hearing.get("case_number") or ""
        if next_date and case_number:
            cur.execute("""
                UPDATE cases SET next_hearing=%s
                WHERE LOWER(TRIM(COALESCE(case_number,'')))=LOWER(TRIM(%s))
                   OR LOWER(TRIM(COALESCE(case_id,'')))=LOWER(TRIM(%s))
            """, (next_date, case_number, case_number))
        if create_task and documents_required.strip() and documents_required.strip().lower() not in {"none","nil","no","-"}:
            cur.execute("""
                INSERT INTO tasks(case_number, assigned_to, task, deadline, status)
                VALUES (%s, %s, %s, %s, 'PENDING') RETURNING id
            """, (case_number, hearing.get("assigned_to"), documents_required.strip(), next_date.isoformat() if next_date else None))
            task_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO hearing_completions(
                live_hearing_id,next_date,next_purpose,order_summary,documents_required,
                task_created_id,notify_client,completed_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (live_hearing_id) DO UPDATE SET
                next_date=EXCLUDED.next_date,next_purpose=EXCLUDED.next_purpose,
                order_summary=EXCLUDED.order_summary,documents_required=EXCLUDED.documents_required,
                task_created_id=COALESCE(EXCLUDED.task_created_id,hearing_completions.task_created_id),
                notify_client=EXCLUDED.notify_client,completed_by=EXCLUDED.completed_by,
                completed_at=CURRENT_TIMESTAMP
        """, (hearing_id,next_date,next_purpose,order_summary,documents_required,task_id,notify_client,changed_by))
        # Timeline is deliberately direct and schema-compatible with the existing activity centre.
        cur.execute("""
            INSERT INTO client_timeline(
                case_id,case_number,event_type,event_title,event_details,event_status,event_category,
                source_type,source_id,created_by,event_at,is_internal
            ) VALUES (%s,%s,'HEARING_COMPLETED','Hearing completed',%s,%s,'hearing',
                      'LIVE_HEARING',%s,%s,CURRENT_TIMESTAMP,TRUE)
            ON CONFLICT DO NOTHING
        """, (
            case_number, case_number,
            f"Order: {order_summary}\nNext date: {next_date or '-'}\nPurpose: {next_purpose or '-'}\nDocuments: {documents_required or '-'}",
            final_status, str(hearing_id), changed_by,
        ))
        cur.execute("""
            INSERT INTO live_hearing_events(live_hearing_id,old_status,new_status,note,changed_by)
            VALUES (%s,%s,%s,%s,%s)
        """, (hearing_id, hearing.get('status'), final_status, order_summary, changed_by))
        conn.commit()
        return {**hearing, "status": final_status, "next_date": next_date, "next_purpose": next_purpose,
                "order_summary": order_summary, "documents_required": documents_required,
                "task_id": task_id, "notify_client": notify_client}
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()
