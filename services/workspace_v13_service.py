"""Sprint 13 case workspace and assigned work management."""
from __future__ import annotations

from datetime import date
from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-bot-s13")


def ensure_schema() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS case_works (
                    id SERIAL PRIMARY KEY,
                    case_record_id INTEGER,
                    case_number TEXT NOT NULL,
                    live_hearing_id INTEGER,
                    title TEXT NOT NULL,
                    details TEXT,
                    assigned_to TEXT,
                    due_date DATE,
                    priority TEXT DEFAULT 'NORMAL',
                    status TEXT DEFAULT 'PENDING',
                    source TEXT DEFAULT 'HEARING_COMPLETION',
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for sql in (
                "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS completed_by BIGINT",
                "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
                "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS completion_note TEXT",
            ):
                cur.execute(sql)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS case_hearing_timeline (
                    id SERIAL PRIMARY KEY,
                    case_record_id INTEGER,
                    case_number TEXT NOT NULL,
                    live_hearing_id INTEGER,
                    event_date DATE NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT,
                    outcome TEXT,
                    next_hearing_date DATE,
                    next_purpose TEXT,
                    preparation TEXT,
                    court_name TEXT,
                    judge_name TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()
    finally:
        conn.close()


def search_cases(term: str, limit: int = 12) -> list[dict[str, Any]]:
    q = f"%{term.strip()}%"
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, case_number, case_id, case_title, client_name, mobile,
                       court_name, judge_name, next_hearing, hearing_date, next_purpose,
                       status, drive_folder_link, fee_agreed, advance_received
                FROM cases
                WHERE COALESCE(case_number,'') ILIKE %s OR COALESCE(case_id,'') ILIKE %s
                   OR COALESCE(case_title,'') ILIKE %s OR COALESCE(client_name,'') ILIKE %s
                ORDER BY CASE WHEN COALESCE(status,'OPEN')='OPEN' THEN 0 ELSE 1 END, id DESC
                LIMIT %s
            """, (q,q,q,q,limit))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def recent_cases(limit: int = 10) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, case_number, case_id, case_title, client_name, mobile,
                       court_name, judge_name, next_hearing, hearing_date, next_purpose,
                       status, drive_folder_link, fee_agreed, advance_received
                FROM cases ORDER BY CASE WHEN COALESCE(status,'OPEN')='OPEN' THEN 0 ELSE 1 END, id DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_case(case_id: int) -> dict[str, Any] | None:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cases WHERE id=%s", (case_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def case_metrics(case_id: int, case_number: str) -> dict[str, Any]:
    ensure_schema()
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT COUNT(*) FILTER (WHERE UPPER(COALESCE(status,'PENDING'))<>'COMPLETED') pending,
                                  COUNT(*) FILTER (WHERE UPPER(COALESCE(status,'PENDING'))='COMPLETED') completed,
                                  COUNT(*) FILTER (WHERE UPPER(COALESCE(status,'PENDING'))<>'COMPLETED' AND due_date<CURRENT_DATE) overdue
                           FROM case_works WHERE case_record_id=%s OR case_number=%s""", (case_id,case_number))
            work = dict(cur.fetchone())
            cur.execute("SELECT COUNT(*) total FROM case_hearing_timeline WHERE case_record_id=%s OR case_number=%s", (case_id,case_number))
            timeline = int(cur.fetchone()["total"])
            cur.execute("SELECT COUNT(*) total FROM documents WHERE case_id=%s OR case_number=%s", (str(case_id),case_number))
            docs = int(cur.fetchone()["total"])
            cur.execute("SELECT COALESCE(SUM(amount),0) total FROM fee_installments WHERE case_number=%s", (case_number,))
            receipts = cur.fetchone()["total"]
            return {**work, "timeline":timeline, "documents":docs, "receipts":receipts}
    except psycopg2.Error:
        conn.rollback()
        return {"pending":0,"completed":0,"overdue":0,"timeline":0,"documents":0,"receipts":0}
    finally:
        conn.close()


def list_works(*, status: str = "PENDING", assigned_to: str | None = None, case_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    ensure_schema()
    clauses=[]; params=[]
    if status != "ALL":
        clauses.append("UPPER(COALESCE(w.status,'PENDING'))=%s"); params.append(status.upper())
    if assigned_to:
        clauses.append("LOWER(COALESCE(w.assigned_to,''))=LOWER(%s)"); params.append(assigned_to)
    if case_id:
        clauses.append("w.case_record_id=%s"); params.append(case_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    conn=_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT w.*, c.case_title, c.next_hearing
                FROM case_works w LEFT JOIN cases c ON c.id=w.case_record_id
                {where}
                ORDER BY CASE WHEN w.due_date<CURRENT_DATE AND UPPER(COALESCE(w.status,'PENDING'))<>'COMPLETED' THEN 0 ELSE 1 END,
                         CASE UPPER(COALESCE(w.priority,'NORMAL')) WHEN 'URGENT' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END,
                         COALESCE(w.due_date, CURRENT_DATE + 36500), w.id DESC
                LIMIT %s
            """, tuple(params))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def staff_name_for_user(telegram_user_id: int) -> str | None:
    conn=_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT staff_name FROM staff_accounts WHERE telegram_user_id=%s AND is_active=TRUE LIMIT 1", (telegram_user_id,))
            row=cur.fetchone(); return str(row["staff_name"]) if row else None
    except psycopg2.Error:
        return None
    finally:
        conn.close()


def complete_work(work_id: int, changed_by: int | None, note: str = "Completed from Telegram") -> dict[str, Any] | None:
    ensure_schema(); conn=_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM case_works WHERE id=%s FOR UPDATE", (work_id,))
            work=cur.fetchone()
            if not work: return None
            cur.execute("""UPDATE case_works SET status='COMPLETED', completed_by=%s, completed_at=CURRENT_TIMESTAMP,
                           completion_note=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s RETURNING *""", (changed_by,note,work_id))
            updated=dict(cur.fetchone())
            cur.execute("""INSERT INTO case_hearing_timeline(case_record_id,case_number,live_hearing_id,event_date,event_type,status,outcome,preparation,created_by)
                           VALUES (%s,%s,%s,%s,'WORK_COMPLETED','COMPLETED',%s,%s,%s)""",
                        (updated.get('case_record_id'),updated.get('case_number'),updated.get('live_hearing_id'),date.today(),
                         f"Work completed: {updated.get('title')}",note,changed_by))
        conn.commit(); return updated
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
