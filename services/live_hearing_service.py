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



def _table_columns(cur, table_name: str) -> set[str]:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
    """, (table_name,))
    return {r[0] for r in cur.fetchall()}


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
    """Persist the core outcome atomically; optional mirrors use savepoints and cannot block saving."""
    ensure_live_hearing_tables()
    conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    warnings = []
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
        cur.execute("""
            INSERT INTO hearing_completions(
                live_hearing_id,next_date,next_purpose,order_summary,documents_required,
                notify_client,completed_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (live_hearing_id) DO UPDATE SET
                next_date=EXCLUDED.next_date,next_purpose=EXCLUDED.next_purpose,
                order_summary=EXCLUDED.order_summary,documents_required=EXCLUDED.documents_required,
                notify_client=EXCLUDED.notify_client,completed_by=EXCLUDED.completed_by,
                completed_at=CURRENT_TIMESTAMP
        """, (hearing_id,next_date,next_purpose,order_summary,documents_required,notify_client,changed_by))
        cur.execute("""
            INSERT INTO live_hearing_events(live_hearing_id,old_status,new_status,note,changed_by)
            VALUES (%s,%s,%s,%s,%s)
        """, (hearing_id, hearing.get('status'), final_status, order_summary, changed_by))
        conn.commit()

        case_number = hearing.get("case_number") or ""
        task_id = None
        # Optional case mirror
        if next_date and case_number:
            try:
                cur.execute("SAVEPOINT mirror_case")
                cols = _table_columns(cur, "cases")
                date_col = "next_hearing" if "next_hearing" in cols else ("next_hearing_date" if "next_hearing_date" in cols else None)
                key_cols = [c for c in ("case_number", "case_id") if c in cols]
                if date_col and key_cols:
                    where = " OR ".join([f"LOWER(TRIM(COALESCE({c},'')))=LOWER(TRIM(%s))" for c in key_cols])
                    cur.execute(f"UPDATE cases SET {date_col}=%s WHERE {where}", tuple([next_date] + [case_number]*len(key_cols)))
                cur.execute("RELEASE SAVEPOINT mirror_case"); conn.commit()
            except Exception as exc:
                conn.rollback(); warnings.append(f"Case next date not mirrored: {type(exc).__name__}")

        # Optional task mirror, schema aware
        if create_task and documents_required.strip() and documents_required.strip().lower() not in {"none","nil","no","-"}:
            try:
                cols = _table_columns(cur, "tasks")
                values = {}
                for c in ("case_number", "case_id"):
                    if c in cols: values[c] = case_number
                if "assigned_to" in cols: values["assigned_to"] = hearing.get("assigned_to")
                if "task" in cols: values["task"] = documents_required.strip()
                elif "title" in cols: values["title"] = documents_required.strip()
                elif "description" in cols: values["description"] = documents_required.strip()
                if "deadline" in cols: values["deadline"] = next_date
                elif "due_at" in cols: values["due_at"] = next_date
                elif "due_date" in cols: values["due_date"] = next_date
                if "status" in cols: values["status"] = "PENDING"
                if not any(k in values for k in ("task","title","description")):
                    raise RuntimeError("tasks table has no task text column")
                names=list(values); ph=','.join(['%s']*len(names))
                cur.execute(f"INSERT INTO tasks({','.join(names)}) VALUES ({ph}) RETURNING id", tuple(values[n] for n in names))
                task_id=cur.fetchone()[0]
                cur.execute("UPDATE hearing_completions SET task_created_id=%s WHERE live_hearing_id=%s", (task_id, hearing_id))
                conn.commit()
            except Exception as exc:
                conn.rollback(); warnings.append(f"Follow-up task not created: {type(exc).__name__}: {exc}")

        # Optional timeline mirror
        try:
            cols = _table_columns(cur, "client_timeline")
            if cols:
                values = {}
                candidates = {
                    "case_id": case_number, "case_number": case_number, "event_type": "HEARING_COMPLETED",
                    "event_title": "Hearing completed",
                    "event_details": f"Order: {order_summary}\nNext date: {next_date or '-'}\nPurpose: {next_purpose or '-'}\nDocuments: {documents_required or '-'}",
                    "event_status": final_status, "event_category": "hearing", "source_type": "LIVE_HEARING",
                    "source_id": str(hearing_id), "created_by": changed_by, "is_internal": True,
                }
                values = {k:v for k,v in candidates.items() if k in cols}
                names=list(values); ph=','.join(['%s']*len(names))
                if names:
                    cur.execute(f"INSERT INTO client_timeline({','.join(names)}) VALUES ({ph})", tuple(values[n] for n in names))
                    conn.commit()
        except Exception as exc:
            conn.rollback(); warnings.append(f"Timeline not updated: {type(exc).__name__}")

        # Advocate Diaries outbound write-back. Local save remains authoritative if remote sync fails.
        ad_sync = None
        if case_number:
            try:
                from services.ad_writeback import writeback_hearing
                ad_sync = writeback_hearing(
                    live_hearing_id=hearing_id,
                    case_number=case_number,
                    hearing_date=hearing.get("hearing_date"),
                    next_date=next_date,
                    next_purpose=next_purpose,
                    order_summary=order_summary,
                    documents_required=documents_required,
                )
            except Exception as exc:
                warnings.append(f"Advocate Diaries write-back unavailable: {type(exc).__name__}: {exc}")

        return {**hearing, "status": final_status, "next_date": next_date, "next_purpose": next_purpose,
                "order_summary": order_summary, "documents_required": documents_required,
                "task_id": task_id, "notify_client": notify_client, "warnings": warnings,
                "ad_sync_status": getattr(ad_sync, "status", None),
                "ad_sync_message": getattr(ad_sync, "message", None)}
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()
