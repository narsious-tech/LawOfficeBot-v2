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



def list_active_staff() -> list[str]:
    conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT staff_name FROM staff_accounts
            WHERE is_active=TRUE AND COALESCE(TRIM(staff_name),'')<>''
            ORDER BY LOWER(staff_name)
        """)
        return [str(r[0]).strip() for r in cur.fetchall()]
    except Exception:
        conn.rollback()
        return ["Happy", "Jimmy", "Preet", "Priya"]
    finally:
        cur.close(); conn.close()

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


def _ensure_completion_schema(cur) -> None:
    """Idempotent Sprint 12.2.4 schema used by the completion transaction."""
    # Standardize the legacy cases table without removing old columns.
    for sql in (
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_number TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_title TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_hearing TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_purpose TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS last_hearing_date DATE",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS last_hearing_outcome TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_case_id TEXT",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS updated_by BIGINT",
    ):
        cur.execute(sql)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hearing_completions (
            id SERIAL PRIMARY KEY,
            live_hearing_id INTEGER UNIQUE REFERENCES live_hearings(id) ON DELETE CASCADE,
            case_record_id INTEGER,
            next_date DATE,
            next_purpose TEXT,
            order_summary TEXT,
            documents_required TEXT,
            work_created_id INTEGER,
            task_created_id INTEGER,
            notify_client BOOLEAN DEFAULT FALSE,
            completed_by BIGINT,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("ALTER TABLE hearing_completions ADD COLUMN IF NOT EXISTS case_record_id INTEGER")
    cur.execute("ALTER TABLE hearing_completions ADD COLUMN IF NOT EXISTS work_created_id INTEGER")
    cur.execute("ALTER TABLE hearing_completions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS case_hearing_timeline (
            id SERIAL PRIMARY KEY,
            case_record_id INTEGER,
            case_number TEXT NOT NULL,
            live_hearing_id INTEGER REFERENCES live_hearings(id) ON DELETE SET NULL,
            event_date DATE NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'HEARING_COMPLETED',
            status TEXT,
            outcome TEXT,
            next_hearing_date DATE,
            next_purpose TEXT,
            preparation TEXT,
            court_name TEXT,
            judge_name TEXT,
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(live_hearing_id, event_type)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS case_works (
            id SERIAL PRIMARY KEY,
            case_record_id INTEGER,
            case_number TEXT NOT NULL,
            live_hearing_id INTEGER REFERENCES live_hearings(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            details TEXT,
            assigned_to TEXT,
            due_date DATE,
            priority TEXT DEFAULT 'NORMAL',
            status TEXT DEFAULT 'PENDING',
            source TEXT DEFAULT 'HEARING_COMPLETION',
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(live_hearing_id, source)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hearing_audit_log (
            id SERIAL PRIMARY KEY,
            live_hearing_id INTEGER REFERENCES live_hearings(id) ON DELETE SET NULL,
            case_record_id INTEGER,
            case_number TEXT,
            action TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            old_hearing_date DATE,
            new_hearing_date DATE,
            old_purpose TEXT,
            new_purpose TEXT,
            outcome TEXT,
            changed_by BIGINT,
            source TEXT DEFAULT 'TELEGRAM',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _normalized_case_expr(column: str) -> str:
    return f"LOWER(REGEXP_REPLACE(TRIM(COALESCE({column},'')), '\\s+', '', 'g'))"


def _find_or_create_case(cur, hearing: dict, changed_by: int | None) -> int:
    case_number = (hearing.get("case_number") or "").strip()
    if not case_number:
        raise RuntimeError("Hearing has no case number")
    cur.execute(f"""
        SELECT id FROM cases
        WHERE {_normalized_case_expr('case_number')}={_normalized_case_expr('%s')}
           OR {_normalized_case_expr('case_id')}={_normalized_case_expr('%s')}
        ORDER BY id DESC LIMIT 1 FOR UPDATE
    """, (case_number, case_number))
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur.execute("""
        INSERT INTO cases(case_id, case_number, case_title, court_name, judge_name, hearing_date,
                          status, updated_at, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,'OPEN',CURRENT_TIMESTAMP,%s)
        RETURNING id
    """, (case_number, case_number, hearing.get("case_title"), hearing.get("court_name"),
          hearing.get("judge_name"), str(hearing.get("hearing_date") or ""), changed_by))
    return int(cur.fetchone()["id"])


def complete_live_hearing(
    hearing_id: int,
    *,
    next_date: date | None,
    next_purpose: str,
    order_summary: str,
    documents_required: str,
    work_assigned_to: str | None,
    work_due_date: date | None,
    work_priority: str | None,
    notify_client: bool,
    changed_by: int | None,
):
    """Sprint 12.2.5: save one assigned Work (or none), then verified AD sync."""
    ensure_live_hearing_tables()
    conn = _connect(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_completion_schema(cur)
        cur.execute("SELECT * FROM live_hearings WHERE id=%s FOR UPDATE", (hearing_id,))
        row = cur.fetchone()
        if not row:
            return None
        hearing = dict(row)
        case_number = (hearing.get("case_number") or "").strip()
        final_status = "ADJOURNED" if next_date else "DISPOSED"
        old_status = hearing.get("status")
        old_date = hearing.get("hearing_date")
        old_purpose = hearing.get("stage") or ""
        case_record_id = _find_or_create_case(cur, hearing, changed_by)

        cur.execute("""
            UPDATE live_hearings
            SET status=%s, status_note=%s, completed_at=CURRENT_TIMESTAMP,
                updated_by=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (final_status, order_summary, changed_by, hearing_id))

        cur.execute("""
            UPDATE cases SET
                hearing_date=%s, next_hearing=%s, next_purpose=%s,
                last_hearing_date=%s, last_hearing_outcome=%s,
                status=%s, court_name=COALESCE(NULLIF(%s,''),court_name),
                judge_name=COALESCE(NULLIF(%s,''),judge_name),
                updated_at=CURRENT_TIMESTAMP, updated_by=%s
            WHERE id=%s
        """, (
            next_date.isoformat() if next_date else None,
            next_date.isoformat() if next_date else None,
            next_purpose or None,
            old_date,
            order_summary or None,
            "OPEN" if next_date else "DISPOSED",
            hearing.get("court_name") or "", hearing.get("judge_name") or "",
            changed_by, case_record_id,
        ))

        cur.execute("""
            INSERT INTO case_hearing_timeline(
                case_record_id,case_number,live_hearing_id,event_date,status,outcome,
                next_hearing_date,next_purpose,preparation,court_name,judge_name,created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(live_hearing_id,event_type) DO UPDATE SET
                status=EXCLUDED.status,outcome=EXCLUDED.outcome,
                next_hearing_date=EXCLUDED.next_hearing_date,next_purpose=EXCLUDED.next_purpose,
                preparation=EXCLUDED.preparation,created_by=EXCLUDED.created_by,
                created_at=CURRENT_TIMESTAMP
            RETURNING id
        """, (case_record_id,case_number,hearing_id,old_date,final_status,order_summary,
              next_date,next_purpose,documents_required,hearing.get("court_name"),
              hearing.get("judge_name"),changed_by))
        timeline_id = int(cur.fetchone()["id"])

        work_id = None
        prep = (documents_required or "").strip()
        meaningful_prep = bool(prep and prep.lower() not in {"none","nil","no","-","/none"})
        if meaningful_prep:
            due_date = work_due_date or next_date
            cur.execute("""
                INSERT INTO case_works(case_record_id,case_number,live_hearing_id,title,details,
                    assigned_to,due_date,priority,status,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
                ON CONFLICT(live_hearing_id,source) DO UPDATE SET
                    title=EXCLUDED.title,details=EXCLUDED.details,assigned_to=EXCLUDED.assigned_to,
                    due_date=EXCLUDED.due_date,priority=EXCLUDED.priority,status='PENDING',updated_at=CURRENT_TIMESTAMP
                RETURNING id
            """, (case_record_id,case_number,hearing_id,prep,
                  f"Created from hearing completion. Outcome: {order_summary}",
                  work_assigned_to,due_date,(work_priority or 'NORMAL').upper(),changed_by))
            work_id = int(cur.fetchone()["id"])

        task_id = None

        cur.execute("""
            INSERT INTO hearing_completions(
                live_hearing_id,case_record_id,next_date,next_purpose,order_summary,
                documents_required,work_created_id,task_created_id,notify_client,completed_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(live_hearing_id) DO UPDATE SET
                case_record_id=EXCLUDED.case_record_id,next_date=EXCLUDED.next_date,
                next_purpose=EXCLUDED.next_purpose,order_summary=EXCLUDED.order_summary,
                documents_required=EXCLUDED.documents_required,work_created_id=EXCLUDED.work_created_id,
                task_created_id=EXCLUDED.task_created_id,notify_client=EXCLUDED.notify_client,
                completed_by=EXCLUDED.completed_by,updated_at=CURRENT_TIMESTAMP
        """, (hearing_id,case_record_id,next_date,next_purpose,order_summary,documents_required,
              work_id,task_id,notify_client,changed_by))

        cur.execute("""
            INSERT INTO live_hearing_events(live_hearing_id,old_status,new_status,note,changed_by)
            VALUES (%s,%s,%s,%s,%s)
        """, (hearing_id,old_status,final_status,order_summary,changed_by))
        cur.execute("""
            INSERT INTO hearing_audit_log(
                live_hearing_id,case_record_id,case_number,action,old_status,new_status,
                old_hearing_date,new_hearing_date,old_purpose,new_purpose,outcome,changed_by
            ) VALUES (%s,%s,%s,'HEARING_COMPLETED',%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (hearing_id,case_record_id,case_number,old_status,final_status,old_date,next_date,
              old_purpose,next_purpose,order_summary,changed_by))
        audit_id = int(cur.fetchone()["id"])
        conn.commit()

        # External system cannot participate in the PostgreSQL transaction. It is attempted only
        # after a complete local commit; its existing retry queue remains authoritative on failure.
        ad_sync = None
        try:
            from services.ad_writeback import writeback_hearing
            ad_sync = writeback_hearing(
                live_hearing_id=hearing_id,case_number=case_number,
                hearing_date=hearing.get("hearing_date"),next_date=next_date,
                next_purpose=next_purpose,order_summary=order_summary,
                documents_required=documents_required,
            )
        except Exception as exc:
            # Local completion is valid; surface the external failure without corrupting it.
            class _SyncFailure:
                status = "QUEUED"
                message = f"{type(exc).__name__}: {exc}"
            ad_sync = _SyncFailure()

        return {
            **hearing,"status":final_status,"next_date":next_date,"next_purpose":next_purpose,
            "order_summary":order_summary,"documents_required":documents_required,
            "case_record_id":case_record_id,"timeline_id":timeline_id,"work_id":work_id,
            "task_id":task_id,"audit_id":audit_id,"notify_client":notify_client,"warnings":[],
            "ad_sync_status":getattr(ad_sync,"status",None),
            "ad_sync_message":getattr(ad_sync,"message",None),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()

