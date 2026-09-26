"""Read-only WhatsApp staff companion backed by the shared Office OS database."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.staff_activity_service import (
    ensure_staff_activity_schema,
    record_staff_activity,
)
from services.whatsapp_cloud import normalize_phone

CLOSED = ("COMPLETED", "COMPLETE", "DONE", "CLOSED", "CANCELLED", "VERIFIED")
OFFICE_TZ = ZoneInfo("Asia/Kolkata")


def _deadline_date(value: Any) -> date | None:
    """Parse current and legacy task deadlines without failing on free text."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def staff_companion_enabled() -> bool:
    return os.getenv("WHATSAPP_STAFF_COMPANION_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def classify_staff_command(text: str) -> tuple[str, str]:
    command = re.sub(r"\s+", " ", str(text or "")).strip()
    upper = command.upper()
    if upper in {"MENU", "HI", "HELLO", "START", "HELP", "/START"}:
        return "MENU", ""
    if upper in {"MY WORK", "WORK", "MYWORK", "TASKS"}:
        return "MY_WORK", ""
    if upper in {"OFFICE STATUS", "STATUS", "MY STATUS"}:
        return "OFFICE_STATUS", ""
    if upper.startswith("CASE "):
        return "CASE", command[5:].strip()
    if upper in {"CHECK IN", "CHECKIN", "CHECK OUT", "CHECKOUT"}:
        return "ATTENDANCE", ""
    return "MENU", ""


def ensure_whatsapp_staff_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE staff_accounts
                ADD COLUMN IF NOT EXISTS whatsapp_phone TEXT
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS staff_accounts_whatsapp_phone_uidx
                ON staff_accounts(whatsapp_phone)
                WHERE whatsapp_phone IS NOT NULL
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_staff_link_audit (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_user_id BIGINT,
                    staff_name TEXT NOT NULL,
                    whatsapp_phone TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_telegram_user_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()
    finally:
        conn.close()
    ensure_staff_activity_schema()


def link_staff_phone(staff_name: str, phone: str, actor_id: int) -> dict[str, Any]:
    ensure_whatsapp_staff_schema()
    normalized = normalize_phone(phone)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.whatsapp_owner_link') AS owner_table")
            if cur.fetchone().get("owner_table"):
                cur.execute(
                    "SELECT 1 FROM whatsapp_owner_link WHERE whatsapp_phone=%s",
                    (normalized,),
                )
                if cur.fetchone():
                    raise ValueError("This number is linked to the office owner.")
            cur.execute("""
                SELECT telegram_user_id, staff_name
                FROM staff_accounts
                WHERE LOWER(TRIM(staff_name))=LOWER(TRIM(%s))
                  AND COALESCE(is_active,TRUE)=TRUE
                LIMIT 1 FOR UPDATE
            """, (staff_name,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Active linked staff member was not found.")
            cur.execute("""
                SELECT staff_name FROM staff_accounts
                WHERE whatsapp_phone=%s AND telegram_user_id<>%s
            """, (normalized, row["telegram_user_id"]))
            duplicate = cur.fetchone()
            if duplicate:
                raise ValueError(
                    f"This WhatsApp number is already linked to {duplicate['staff_name']}."
                )
            cur.execute("""
                UPDATE staff_accounts SET whatsapp_phone=%s
                WHERE telegram_user_id=%s
            """, (normalized, row["telegram_user_id"]))
            cur.execute("""
                INSERT INTO whatsapp_staff_link_audit
                    (telegram_user_id,staff_name,whatsapp_phone,action,
                     actor_telegram_user_id)
                VALUES (%s,%s,%s,'LINK',%s)
            """, (
                row["telegram_user_id"], row["staff_name"], normalized, actor_id,
            ))
        conn.commit()
        return {
            "telegram_user_id": int(row["telegram_user_id"]),
            "staff_name": row["staff_name"], "phone": normalized,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def unlink_staff_phone(phone: str, actor_id: int) -> dict[str, Any]:
    ensure_whatsapp_staff_schema()
    normalized = normalize_phone(phone)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE staff_accounts SET whatsapp_phone=NULL
                WHERE whatsapp_phone=%s
                RETURNING telegram_user_id,staff_name
            """, (normalized,))
            row = cur.fetchone()
            if not row:
                raise ValueError("No staff account is linked to that WhatsApp number.")
            cur.execute("""
                INSERT INTO whatsapp_staff_link_audit
                    (telegram_user_id,staff_name,whatsapp_phone,action,
                     actor_telegram_user_id)
                VALUES (%s,%s,%s,'UNLINK',%s)
            """, (
                row["telegram_user_id"], row["staff_name"], normalized, actor_id,
            ))
        conn.commit()
        return {"staff_name": row["staff_name"], "phone": normalized}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def linked_staff_phones() -> list[dict[str, Any]]:
    ensure_whatsapp_staff_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT telegram_user_id,staff_name,whatsapp_phone
                FROM staff_accounts
                WHERE whatsapp_phone IS NOT NULL AND COALESCE(is_active,TRUE)=TRUE
                ORDER BY LOWER(staff_name)
            """)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _staff_for_phone(cur, phone: str) -> dict[str, Any] | None:
    cur.execute("""
        SELECT telegram_user_id,staff_name
        FROM staff_accounts
        WHERE whatsapp_phone=%s AND COALESCE(is_active,TRUE)=TRUE
        LIMIT 1
    """, (normalize_phone(phone),))
    row = cur.fetchone()
    return dict(row) if row else None


def _my_work(cur, staff_name: str) -> str:
    cur.execute("SELECT to_regclass('public.tasks') AS task_table")
    table_row = cur.fetchone()
    if not table_row or not table_row.get("task_table"):
        return "📋 My Work\n\nThe Office OS task table is not available."
    cur.execute("""
        SELECT t.id AS task_id,
               t.task AS task_text,
               t.case_number AS task_case_number,
               t.deadline AS deadline,
               t.due_at AS due_at,
               COALESCE(c.case_title,'') AS case_title
        FROM tasks t
        LEFT JOIN LATERAL (
            SELECT case_title FROM cases
            WHERE LOWER(TRIM(COALESCE(case_number,'')))=
                  LOWER(TRIM(COALESCE(t.case_number,'')))
               OR LOWER(TRIM(COALESCE(case_id,'')))=
                  LOWER(TRIM(COALESCE(t.case_number,'')))
            ORDER BY id DESC LIMIT 1
        ) c ON TRUE
        WHERE LOWER(TRIM(COALESCE(t.assigned_to,'')))=LOWER(TRIM(%s))
          AND UPPER(COALESCE(t.status,'PENDING'))<>ALL(%s)
        ORDER BY t.due_at NULLS LAST,t.id
        LIMIT 8
    """, (staff_name, list(CLOSED)))
    rows = cur.fetchall()
    if not rows:
        return "✅ My Work\n\nNo pending work is assigned to you."
    lines = ["📋 MY PENDING WORK", ""]
    for row in rows:
        due = row.get("due_at") or row.get("deadline") or "Not fixed"
        lines.extend([
            f"#{row.get('task_id')} · "
            f"{row.get('case_title') or row.get('task_case_number') or 'General office work'}",
            f"📝 {row.get('task_text') or 'Work details not recorded'}",
            f"📅 Due: {due}", "",
        ])
    lines.append("Use Telegram /myworks for full details and completion controls.")
    return "\n".join(lines)[:4000]


def _office_status(cur, staff: dict[str, Any]) -> str:
    cur.execute("""
        SELECT due_at AS due_at, deadline AS deadline
        FROM tasks
        WHERE LOWER(TRIM(COALESCE(assigned_to,'')))=LOWER(TRIM(%s))
          AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)
    """, (staff["staff_name"], list(CLOSED)))
    task_rows = cur.fetchall()
    today = datetime.now(OFFICE_TZ).date()
    pending = len(task_rows)
    overdue = 0
    for row in task_rows:
        due_date = _deadline_date(row.get("due_at") or row.get("deadline"))
        if due_date and due_date < today:
            overdue += 1
    attendance = "Not checked in"
    cur.execute(
        "SELECT to_regclass('public.attendance_sessions') AS attendance_table"
    )
    table_row = cur.fetchone()
    if table_row and table_row.get("attendance_table"):
        cur.execute("""
            SELECT checkin_time AS checkin_time,
                   checkout_time AS checkout_time
            FROM attendance_sessions
            WHERE telegram_user_id=%s AND attendance_date=CURRENT_DATE
            LIMIT 1
        """, (staff["telegram_user_id"],))
        row = cur.fetchone()
        if row:
            attendance = (
                "Checked out" if row.get("checkout_time")
                else "Present / checked in"
            )
    return (
        f"🏢 OFFICE STATUS — {staff['staff_name']}\n\n"
        f"📋 Pending work: {pending}\n"
        f"🔴 Overdue: {overdue}\n"
        f"🕒 Attendance: {attendance}\n\n"
        "Attendance actions and administrative controls remain in Telegram."
    )


def _case_lookup(cur, query: str) -> str:
    needle = query.strip()
    if not needle:
        return "Usage: CASE CS/123/2026"
    cur.execute("""
        SELECT COALESCE(case_number,case_id) AS case_number,
               case_title AS case_title,
               next_hearing AS next_hearing
        FROM cases
        WHERE LOWER(COALESCE(case_number,'')) LIKE LOWER(%s)
           OR LOWER(COALESCE(case_id,'')) LIKE LOWER(%s)
           OR LOWER(COALESCE(case_title,'')) LIKE LOWER(%s)
        ORDER BY id DESC LIMIT 5
    """, (f"%{needle}%", f"%{needle}%", f"%{needle}%"))
    rows = cur.fetchall()
    if not rows:
        return "🔎 No Office OS case matched that search."
    lines = ["🔎 CASE SEARCH", ""]
    for row in rows:
        number = row.get("case_number")
        title = row.get("case_title")
        next_date = row.get("next_hearing")
        lines.extend([
            f"⚖️ {title or 'Title not recorded'}",
            f"🔢 {number or '-'}",
            f"📅 Next: {next_date or 'Not recorded'}",
            "",
        ])
    return "\n".join(lines)[:4000]


def menu_text(staff_name: str) -> str:
    return (
        f"🏛 LAW OFFICE OF AJAY CHAWLA\n\nWelcome, {staff_name}.\n"
        "Choose a button below or send:\n"
        "• MY WORK\n• OFFICE STATUS\n• CASE <number/title>\n• HELP\n\n"
        "Telegram remains the secure Command Centre for updates and approvals."
    )


def handle_staff_inbound(item: dict[str, Any]) -> dict[str, Any]:
    """Route one persisted inbound message; returns is_staff/reply/menu/activity."""
    ensure_whatsapp_staff_schema()
    phone = normalize_phone(str(item.get("phone") or ""))
    incoming = str(item.get("text") or "").strip()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            staff = _staff_for_phone(cur, phone)
            if not staff:
                return {"is_staff": False}
            action, argument = classify_staff_command(incoming)
            menu = action == "MENU"
            if action == "MENU":
                reply = menu_text(staff["staff_name"])
            elif action == "MY_WORK":
                reply = _my_work(cur, staff["staff_name"])
            elif action == "OFFICE_STATUS":
                reply = _office_status(cur, staff)
            elif action == "CASE":
                reply = _case_lookup(cur, argument)
            elif action == "ATTENDANCE":
                reply = (
                    "📍 Attendance requires verified office location. "
                    "Please use Check In / Check Out in the Telegram bot."
                )
            else:
                reply = menu_text(staff["staff_name"])
                menu = True
    finally:
        conn.close()

    digest = hashlib.sha256(
        str(item.get("provider_message_id") or "").encode()
    ).digest()
    update_id = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    activity_id = record_staff_activity(
        update_id=update_id,
        event_kind="WHATSAPP_MESSAGE",
        user_id=int(staff["telegram_user_id"]),
        staff_name=str(staff["staff_name"]),
        staff_role="staff",
        chat_id=None,
        chat_type="whatsapp_private",
        chat_title="WhatsApp Staff Companion",
        summary=incoming[:3000] or f"[{item.get('type') or 'message'}]",
        metadata={"phone": phone, "provider_message_id": item.get("provider_message_id")},
    )
    return {
        "is_staff": True, "staff": staff, "reply": reply, "menu": menu,
        "activity_id": activity_id, "incoming": incoming, "phone": phone,
    }
