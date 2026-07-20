"""Sprint 14 dynamic court-floor case ownership and Work supervision."""
from __future__ import annotations
import re
from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

DEFAULT_OWNER = "Preet"

def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-bot-s14")

def normalize_floor(value: Any) -> int | None:
    if value is None: return None
    m = re.search(r"-?\d+", str(value))
    if not m: return None
    n = int(m.group())
    return n if 0 <= n <= 99 else None

def owner_for_floor(value: Any) -> str:
    floor = normalize_floor(value)
    if floor is not None and 4 <= floor <= 6:
        return "Happy"
    return DEFAULT_OWNER

def ensure_schema() -> None:
    conn=_connect()
    try:
      with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS case_ownership (
          id SERIAL PRIMARY KEY, case_record_id INTEGER, case_number TEXT NOT NULL,
          owner_staff TEXT NOT NULL, assignment_mode TEXT NOT NULL DEFAULT 'AUTO_FLOOR',
          source_floor INTEGER, source_court TEXT, source_judge TEXT,
          manual_override BOOLEAN DEFAULT FALSE, active BOOLEAN DEFAULT TRUE,
          assigned_by BIGINT, assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(case_number, active)
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS case_ownership_history (
          id SERIAL PRIMARY KEY, case_record_id INTEGER, case_number TEXT NOT NULL,
          old_owner TEXT, new_owner TEXT NOT NULL, old_floor INTEGER, new_floor INTEGER,
          reason TEXT NOT NULL, assignment_mode TEXT NOT NULL,
          changed_by BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS work_supervision_events (
          id SERIAL PRIMARY KEY, work_id INTEGER NOT NULL, action TEXT NOT NULL,
          old_value TEXT, new_value TEXT, changed_by BIGINT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        for sql in (
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS supervisor TEXT DEFAULT 'Priya'",
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS assignment_source TEXT DEFAULT 'CASE_OWNER'",
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMP",
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP",
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP",
          "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS verified_by BIGINT",
        ): cur.execute(sql)
      conn.commit()
    finally: conn.close()

def assign_case(case_number: str, floor: Any, *, case_record_id: int|None=None, court: str|None=None, judge: str|None=None, changed_by: int|None=None) -> dict:
    ensure_schema(); new_floor=normalize_floor(floor); new_owner=owner_for_floor(floor)
    conn=_connect()
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM case_ownership WHERE case_number=%s AND active=TRUE FOR UPDATE",(case_number,))
        old=cur.fetchone()
        if old and old.get('manual_override'):
          conn.commit(); return dict(old)
        if old and old['owner_staff']==new_owner and old.get('source_floor')==new_floor:
          cur.execute("UPDATE case_ownership SET source_court=%s,source_judge=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s RETURNING *",(court,judge,old['id']))
          row=dict(cur.fetchone()); conn.commit(); return row
        old_owner=old.get('owner_staff') if old else None; old_floor=old.get('source_floor') if old else None
        if old: cur.execute("UPDATE case_ownership SET active=FALSE,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(old['id'],))
        reason = f"Court floor {new_floor}" if new_floor is not None else "Missing/invalid floor fallback to Preet"
        cur.execute("""INSERT INTO case_ownership(case_record_id,case_number,owner_staff,assignment_mode,source_floor,source_court,source_judge,assigned_by)
          VALUES(%s,%s,%s,'AUTO_FLOOR',%s,%s,%s,%s) RETURNING *""",(case_record_id,case_number,new_owner,new_floor,court,judge,changed_by))
        row=dict(cur.fetchone())
        cur.execute("""INSERT INTO case_ownership_history(case_record_id,case_number,old_owner,new_owner,old_floor,new_floor,reason,assignment_mode,changed_by)
          VALUES(%s,%s,%s,%s,%s,%s,%s,'AUTO_FLOOR',%s)""",(case_record_id,case_number,old_owner,new_owner,old_floor,new_floor,reason,changed_by))
      conn.commit(); return row
    except Exception: conn.rollback(); raise
    finally: conn.close()

def _latest_mirrored_hearing(case_number: str) -> dict | None:
    """Return the latest Advocate Diaries-mirrored hearing with resolved court data."""
    conn = _connect()
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
          SELECT case_number, floor, court_name, judge_name, hearing_date
          FROM live_hearings
          WHERE LOWER(TRIM(case_number)) = LOWER(TRIM(%s))
          ORDER BY
            CASE WHEN NULLIF(TRIM(COALESCE(floor,'')),'') IS NOT NULL THEN 0 ELSE 1 END,
            hearing_date DESC, updated_at DESC
          LIMIT 1
        """, (case_number,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
      conn.close()

def get_case_owner(case_number: str, floor: Any=None, *, case_record_id: int|None=None, court: str|None=None, judge: str|None=None) -> dict:
    # Reuse the Advocate Diaries mirror used by the cause list and morning dashboard.
    # This also prevents a workspace record with no local floor from overwriting a
    # previously correct automatic ownership assignment with the Preet fallback.
    resolved_floor = normalize_floor(floor)
    if resolved_floor is None and case_number:
      mirrored = _latest_mirrored_hearing(case_number)
      if mirrored and normalize_floor(mirrored.get('floor')) is not None:
        floor = mirrored.get('floor')
        court = mirrored.get('court_name') or court
        judge = mirrored.get('judge_name') or judge
        resolved_floor = normalize_floor(floor)

    if resolved_floor is None:
      conn = _connect()
      try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
          cur.execute("SELECT * FROM case_ownership WHERE case_number=%s AND active=TRUE", (case_number,))
          existing = cur.fetchone()
          if existing and existing.get('source_floor') is not None:
            return dict(existing)
      finally:
        conn.close()

    return assign_case(case_number,floor,case_record_id=case_record_id,court=court,judge=judge)

def reconcile_live_hearings() -> int:
    # Refresh the same normalized Advocate Diaries mirror used by the morning
    # dashboard/cause list before calculating ownership.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from services.live_hearing_service import sync_live_hearings
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    sync_live_hearings(today)

    ensure_schema(); conn=_connect(); count=0
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT case_number,floor,court_name,judge_name FROM live_hearings WHERE hearing_date=%s", (today,))
        rows=[dict(r) for r in cur.fetchall()]
      conn.close(); conn=None
      for r in rows:
        if r.get('case_number'):
          get_case_owner(r['case_number'],r.get('floor'),court=r.get('court_name'),judge=r.get('judge_name')); count+=1
      return count
    finally:
      if conn: conn.close()

def supervision_summary() -> dict:
    ensure_schema(); conn=_connect()
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT
          COUNT(*) FILTER(WHERE UPPER(status) NOT IN ('COMPLETED','VERIFIED','CLOSED')) pending,
          COUNT(*) FILTER(WHERE due_date=CURRENT_DATE AND UPPER(status) NOT IN ('COMPLETED','VERIFIED','CLOSED')) due_today,
          COUNT(*) FILTER(WHERE due_date<CURRENT_DATE AND UPPER(status) NOT IN ('COMPLETED','VERIFIED','CLOSED')) overdue,
          COUNT(*) FILTER(WHERE UPPER(status)='COMPLETED') awaiting_verification,
          COUNT(*) FILTER(WHERE verified_at::date=CURRENT_DATE) verified_today
          FROM case_works""")
        total=dict(cur.fetchone())
        cur.execute("""SELECT COALESCE(assigned_to,'Unassigned') staff,
          COUNT(*) FILTER(WHERE UPPER(status) NOT IN ('COMPLETED','VERIFIED','CLOSED')) pending,
          COUNT(*) FILTER(WHERE due_date<CURRENT_DATE AND UPPER(status) NOT IN ('COMPLETED','VERIFIED','CLOSED')) overdue
          FROM case_works GROUP BY assigned_to ORDER BY pending DESC""")
        total['staff']=[dict(r) for r in cur.fetchall()]
        return total
    finally: conn.close()
