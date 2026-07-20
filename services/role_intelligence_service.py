from __future__ import annotations
import os
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor

CLOSED = ("COMPLETED","COMPLETE","DONE","CLOSED","CANCELLED","VERIFIED")

def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def ensure_schema() -> None:
    with _connect() as con, con.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS physical_file_assignments (
          id BIGSERIAL PRIMARY KEY,
          assignment_date DATE NOT NULL,
          case_number TEXT NOT NULL,
          case_title TEXT,
          court TEXT,
          judge TEXT,
          floor TEXT,
          room TEXT,
          purpose TEXT,
          assigned_by_telegram BIGINT,
          assigned_by_name TEXT,
          status TEXT NOT NULL DEFAULT 'SELECTED',
          status_by_telegram BIGINT,
          status_by_name TEXT,
          status_note TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE(assignment_date, case_number)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pfa_date_status ON physical_file_assignments(assignment_date,status)")

def save_file_assignments(target: date, cases: list[dict], selected: set[int], assigned_by_id: int, assigned_by_name: str) -> None:
    ensure_schema()
    with _connect() as con, con.cursor() as cur:
        for idx in sorted(selected):
            c=cases[idx]
            cur.execute("""
            INSERT INTO physical_file_assignments
            (assignment_date,case_number,case_title,court,judge,floor,room,purpose,assigned_by_telegram,assigned_by_name,status,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SELECTED',NOW())
            ON CONFLICT (assignment_date,case_number) DO UPDATE SET
              case_title=EXCLUDED.case_title,court=EXCLUDED.court,judge=EXCLUDED.judge,
              floor=EXCLUDED.floor,room=EXCLUDED.room,purpose=EXCLUDED.purpose,
              assigned_by_telegram=EXCLUDED.assigned_by_telegram,assigned_by_name=EXCLUDED.assigned_by_name,
              status='SELECTED',status_by_telegram=NULL,status_by_name=NULL,status_note=NULL,updated_at=NOW()
            """,(target,c['case_number'],c['case_title'],c['court'],c['judge'],c['floor'],c['room'],c['purpose'],assigned_by_id,assigned_by_name))

def file_assignments(target: date) -> list[dict]:
    ensure_schema()
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM physical_file_assignments WHERE assignment_date=%s ORDER BY id",(target,))
        return [dict(r) for r in cur.fetchall()]

def update_file_status(assignment_id:int,status:str,user_id:int,user_name:str,note:str|None=None)->dict|None:
    ensure_schema(); status=status.upper()
    allowed={'SELECTED','BROUGHT','NOT_FOUND','NEEDS_ATTENTION'}
    if status not in allowed: raise ValueError('Invalid file status')
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""UPDATE physical_file_assignments SET status=%s,status_by_telegram=%s,status_by_name=%s,status_note=%s,updated_at=NOW()
        WHERE id=%s RETURNING *""",(status,user_id,user_name,note,assignment_id))
        row=cur.fetchone(); return dict(row) if row else None

def staff_profile(telegram_id:int)->dict:
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute("SELECT staff_name,role,is_active FROM staff_accounts WHERE telegram_user_id=%s LIMIT 1",(telegram_id,))
        except Exception:
            con.rollback(); cur.execute("SELECT staff_name,is_active FROM staff_accounts WHERE telegram_user_id=%s LIMIT 1",(telegram_id,))
        r=cur.fetchone()
        if not r: return {'staff_name':'Staff','role':'staff','is_active':True}
        d=dict(r); d['role']=str(d.get('role') or 'staff').lower(); return d

def role_dashboard(telegram_id:int)->dict:
    ensure_schema(); p=staff_profile(telegram_id); name=p['staff_name']; role=p['role']
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT COUNT(*) total,
        COUNT(*) FILTER(WHERE status='BROUGHT') brought,
        COUNT(*) FILTER(WHERE status='NOT_FOUND') not_found,
        COUNT(*) FILTER(WHERE status='NEEDS_ATTENTION') attention,
        COUNT(*) FILTER(WHERE status='SELECTED') pending
        FROM physical_file_assignments WHERE assignment_date>=CURRENT_DATE""")
        files=dict(cur.fetchone())
        works={'pending':0,'overdue':0,'due_today':0}
        try:
            if role in ('admin','owner','principal','supervisor','manager') or name.lower() in ('ajay','priya'):
                cur.execute("""SELECT COUNT(*) FILTER(WHERE UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) pending,
                COUNT(*) FILTER(WHERE due_date<CURRENT_DATE AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) overdue,
                COUNT(*) FILTER(WHERE due_date=CURRENT_DATE AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) due_today FROM case_works""",(list(CLOSED),list(CLOSED),list(CLOSED)))
            else:
                cur.execute("""SELECT COUNT(*) FILTER(WHERE UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) pending,
                COUNT(*) FILTER(WHERE due_date<CURRENT_DATE AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) overdue,
                COUNT(*) FILTER(WHERE due_date=CURRENT_DATE AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)) due_today FROM case_works WHERE LOWER(TRIM(COALESCE(assigned_to,'')))=LOWER(TRIM(%s))""",(list(CLOSED),list(CLOSED),list(CLOSED),name))
            works=dict(cur.fetchone())
        except Exception:
            con.rollback()
        pending_updates=0
        try:
            from services.case_intelligence_service import advocate_diaries_pending_cases
            pending_updates=len(advocate_diaries_pending_cases())
        except Exception: pass
    return {'profile':p,'files':files,'works':works,'pending_updates':pending_updates}
