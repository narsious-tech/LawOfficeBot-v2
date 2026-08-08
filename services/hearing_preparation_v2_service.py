from __future__ import annotations
import os
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

STEPS = ("FILE","DOCUMENTS","ORDER","INSTRUCTIONS")
READY_STATUSES = {"READY","NOT_REQUIRED"}
FILE_READY = {"BROUGHT","NOT_REQUIRED"}

def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])



def resolve_target_date(base: date | None = None) -> date:
    """Return the next date that actually has selected physical-file assignments.

    This keeps /preparation aligned with /eveningdashboard on weekends/holidays
    instead of blindly assuming calendar tomorrow. Falls back to tomorrow when
    no assignment exists yet.
    """
    base = base or date.today()
    tomorrow = base + timedelta(days=1)
    try:
        with _connect() as con, con.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(assignment_date)
                FROM physical_file_assignments
                WHERE assignment_date >= %s
                  AND assignment_date <= %s
                """,
                (tomorrow, tomorrow + timedelta(days=7)),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
    except psycopg2.Error:
        # The physical-file table may not exist before first evening selection.
        # Keep the command usable and let the normal empty-state message guide
        # the user to /eveningdashboard.
        pass
    return tomorrow

def ensure_schema():
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS hearing_preparation (
          id BIGSERIAL PRIMARY KEY,
          hearing_date DATE NOT NULL,
          case_number TEXT NOT NULL,
          case_title TEXT,
          court TEXT,
          judge TEXT,
          floor TEXT,
          room TEXT,
          purpose TEXT,
          physical_file_status TEXT NOT NULL DEFAULT 'PENDING',
          documents_status TEXT NOT NULL DEFAULT 'PENDING',
          previous_order_status TEXT NOT NULL DEFAULT 'PENDING',
          instructions_status TEXT NOT NULL DEFAULT 'PENDING',
          overall_status TEXT NOT NULL DEFAULT 'NOT_READY',
          last_updated_by_telegram BIGINT,
          last_updated_by_name TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE(hearing_date,case_number)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_hp_date_overall ON hearing_preparation(hearing_date,overall_status)")

def _overall(file_status, docs, order, instructions):
    if file_status in ("NOT_FOUND","NEEDS_ATTENTION") or "ATTENTION" in (docs,order,instructions):
        return "ATTENTION"
    if file_status in FILE_READY and docs in READY_STATUSES and order in READY_STATUSES and instructions in READY_STATUSES:
        return "READY"
    return "NOT_READY"

def sync_from_physical_files(target: date):
    ensure_schema()
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT * FROM physical_file_assignments WHERE assignment_date=%s ORDER BY id""",(target,))
        files=[dict(r) for r in cur.fetchall()]
        for r in files:
            cur.execute("""
            INSERT INTO hearing_preparation
            (hearing_date,case_number,case_title,court,judge,floor,room,purpose,physical_file_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (hearing_date,case_number) DO UPDATE SET
              case_title=EXCLUDED.case_title,court=EXCLUDED.court,judge=EXCLUDED.judge,
              floor=EXCLUDED.floor,room=EXCLUDED.room,purpose=EXCLUDED.purpose,
              physical_file_status=EXCLUDED.physical_file_status,updated_at=NOW()
            """,(target,r["case_number"],r.get("case_title"),r.get("court"),r.get("judge"),r.get("floor"),r.get("room"),r.get("purpose"),("PENDING" if (r.get("status") or "SELECTED").upper()=="SELECTED" else r.get("status"))))
        cur.execute("SELECT * FROM hearing_preparation WHERE hearing_date=%s ORDER BY id",(target,))
        rows=[dict(r) for r in cur.fetchall()]
        for r in rows:
            overall=_overall(r["physical_file_status"],r["documents_status"],r["previous_order_status"],r["instructions_status"])
            if overall != r["overall_status"]:
                cur.execute("UPDATE hearing_preparation SET overall_status=%s,updated_at=NOW() WHERE id=%s",(overall,r["id"]))
                r["overall_status"]=overall
        return rows

def preparation_rows(target: date):
    return sync_from_physical_files(target)

def update_step(row_id:int, step:str, status:str, user_id:int, user_name:str):
    ensure_schema()
    columns={"FILE":"physical_file_status","DOCUMENTS":"documents_status","ORDER":"previous_order_status","INSTRUCTIONS":"instructions_status"}
    col=columns.get(step.upper())
    if not col: raise ValueError("Invalid preparation step")
    allowed={
      "FILE":{"BROUGHT","NOT_FOUND","NEEDS_ATTENTION","NOT_REQUIRED","PENDING"},
      "DOCUMENTS":{"READY","ATTENTION","NOT_REQUIRED","PENDING"},
      "ORDER":{"READY","ATTENTION","NOT_REQUIRED","PENDING"},
      "INSTRUCTIONS":{"READY","ATTENTION","NOT_REQUIRED","PENDING"},
    }
    status=status.upper()
    if status not in allowed[step.upper()]: raise ValueError("Invalid preparation status")
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"UPDATE hearing_preparation SET {col}=%s,last_updated_by_telegram=%s,last_updated_by_name=%s,updated_at=NOW() WHERE id=%s RETURNING *",(status,user_id,user_name,row_id))
        r=cur.fetchone()
        if not r: return None
        d=dict(r)
        overall=_overall(d["physical_file_status"],d["documents_status"],d["previous_order_status"],d["instructions_status"])
        cur.execute("UPDATE hearing_preparation SET overall_status=%s,updated_at=NOW() WHERE id=%s RETURNING *",(overall,row_id))
        return dict(cur.fetchone())

def summary(target: date):
    rows=preparation_rows(target)
    counts={"total":len(rows),"ready":0,"not_ready":0,"attention":0,"files_required":0,"files_brought":0,"files_missing":0}
    for r in rows:
        o=r["overall_status"]
        if o=="READY": counts["ready"]+=1
        elif o=="ATTENTION": counts["attention"]+=1
        else: counts["not_ready"]+=1
        if r["physical_file_status"]!="NOT_REQUIRED": counts["files_required"]+=1
        if r["physical_file_status"]=="BROUGHT": counts["files_brought"]+=1
        if r["physical_file_status"]=="NOT_FOUND": counts["files_missing"]+=1
    return counts
