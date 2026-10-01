"""Read-only WhatsApp staff companion backed by the shared Office OS database."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta
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
    if upper in {"MENU", "HI", "HELLO", "START", "/START"}:
        return "MENU", ""
    if upper in {"HELP", "HOW TO USE"}:
        return "HELP", ""
    if upper in {"MORNING", "MORNING DASHBOARD", "MORNING BRIEF"}:
        return "MORNING_DASHBOARD", ""
    if upper in {"EVENING", "EVENING DASHBOARD", "DAY CLOSING", "DAY CLOSING DASHBOARD"}:
        return "EVENING_DASHBOARD", ""
    if upper in {"MY WORK", "WORK", "MYWORK", "TASKS", "MY TASKS"}:
        return "MY_WORK", ""
    if upper in {"OFFICE STATUS", "STATUS", "MY STATUS"}:
        return "OFFICE_STATUS", ""
    if upper in {"TODAY", "TODAY HEARINGS", "TODAY'S HEARINGS", "HEARINGS TODAY"}:
        return "TODAY_HEARINGS", ""
    if upper in {"TOMORROW", "TOMORROW HEARINGS", "TOMORROW'S HEARINGS", "HEARINGS TOMORROW"}:
        return "TOMORROW_HEARINGS", ""
    if upper in {"CASE SEARCH", "SEARCH CASE"}:
        return "CASE_PROMPT", ""
    if upper.startswith("CASE "):
        return "CASE", command[5:].strip()
    if upper in {"ATTENDANCE", "ATTENDANCE STATUS", "MY ATTENDANCE"}:
        return "ATTENDANCE_STATUS", ""
    if upper in {"CHECK IN", "CHECKIN"}:
        return "ATTENDANCE_BEGIN", "CHECKIN"
    if upper in {"CHECK OUT", "CHECKOUT"}:
        return "ATTENDANCE_BEGIN", "CHECKOUT"
    if upper in {"CANCEL", "STOP"}:
        return "ATTENDANCE_CANCEL", ""
    if upper.startswith("DONE ") or upper.startswith("COMPLETE "):
        task_id = command.split(" ", 1)[1].strip()
        return "TASK_SELECT", task_id
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
                ALTER TABLE staff_accounts
                ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'staff'
            """)
            cur.execute("""
                ALTER TABLE staff_accounts
                ADD COLUMN IF NOT EXISTS attendance_office_scope TEXT DEFAULT 'ALL'
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
        SELECT telegram_user_id,staff_name,COALESCE(role,'staff') AS role,
               COALESCE(attendance_office_scope,'ALL') AS attendance_office_scope
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


def _attendance_status(cur, staff: dict[str, Any]) -> str:
    cur.execute(
        "SELECT to_regclass('public.attendance_sessions') AS attendance_table"
    )
    table_row = cur.fetchone()
    if not table_row or not table_row.get("attendance_table"):
        return "🕒 ATTENDANCE\n\nAttendance records are not available."
    cur.execute("""
        SELECT checkin_time AS checkin_time, checkout_time AS checkout_time
        FROM attendance_sessions
        WHERE telegram_user_id=%s AND attendance_date=CURRENT_DATE
        LIMIT 1
    """, (staff["telegram_user_id"],))
    row = cur.fetchone()
    if not row:
        return "🕒 ATTENDANCE\n\nYou have not checked in today."
    if row.get("checkout_time"):
        return (
            f"🕒 ATTENDANCE\n\n✅ Checked in: {row.get('checkin_time')}\n"
            f"🏁 Checked out: {row.get('checkout_time')}"
        )
    return f"🕒 ATTENDANCE\n\n✅ Present\nChecked in: {row.get('checkin_time')}\nCheckout is pending."


def _hearing_replies(days_ahead: int) -> list[str]:
    from services.staff_hearing_service import (
        fetch_staff_hearings,
        hearing_message_chunks,
    )

    target = (datetime.now(OFFICE_TZ) + timedelta(days=days_ahead)).date()
    return hearing_message_chunks(fetch_staff_hearings(target))


def _task_picker_rows(cur, staff_name: str) -> list[dict[str, str]]:
    cur.execute("""
        SELECT id,task,COALESCE(case_number,'') AS case_number,
               COALESCE(due_at::TEXT,deadline,'') AS due
        FROM tasks
        WHERE LOWER(TRIM(COALESCE(assigned_to,'')))=LOWER(TRIM(%s))
          AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)
        ORDER BY due_at NULLS LAST,id
        LIMIT 10
    """, (staff_name, list(CLOSED)))
    rows = []
    for row in cur.fetchall():
        task_id = row.get("id")
        label = str(row.get("task") or "Work")
        description = " · ".join(
            value for value in (
                str(row.get("case_number") or ""), str(row.get("due") or ""),
            ) if value
        )
        rows.append({
            "id": f"staff_task:{task_id}",
            "title": f"#{task_id} {label}",
            "description": description or "Open task details",
        })
    return rows


def _task_for_staff(cur, staff_name: str, task_id: str) -> dict[str, Any] | None:
    if not str(task_id).isdigit():
        return None
    cur.execute("""
        SELECT id,task,case_number,notes,source_type,source_work_id,status,
               COALESCE(due_at::TEXT,deadline,'') AS due
        FROM tasks
        WHERE id=%s
          AND LOWER(TRIM(COALESCE(assigned_to,'')))=LOWER(TRIM(%s))
        LIMIT 1
        FOR UPDATE
    """, (int(task_id), staff_name))
    row = cur.fetchone()
    return dict(row) if row else None


def _task_confirmation(task: dict[str, Any]) -> str:
    return (
        "⚠️ CONFIRM WORK COMPLETION\n\n"
        f"Task #{task['id']}\n"
        f"📝 {task.get('task') or 'No description'}\n"
        f"⚖️ {task.get('case_number') or 'General office work'}\n"
        f"📅 Due: {task.get('due') or 'Not fixed'}\n\n"
        "Confirm only after the work has actually been completed."
    )


def _complete_staff_task(cur, conn, staff: dict[str, Any], task_id: str) -> str:
    task = _task_for_staff(cur, staff["staff_name"], task_id)
    if not task:
        return "❌ Task not found or it is not assigned to you."
    if str(task.get("status") or "").upper() in CLOSED:
        return f"ℹ️ Task #{task['id']} is already completed or closed."

    source_type = str(task.get("source_type") or "manual").lower()
    source_work_id = task.get("source_work_id")
    if source_type == "advocate_diaries_work" and source_work_id:
        try:
            from advocate_web import AdvocateWeb

            response = AdvocateWeb().complete_work(str(source_work_id))
            if response.status_code != 200:
                return (
                    "❌ Advocate Diaries completion failed.\n"
                    f"Status: {response.status_code}\n\n"
                    "The Office OS task was not changed. Please retry or use Telegram."
                )
        except Exception as exc:
            return (
                "❌ Advocate Diaries completion failed.\n"
                f"{type(exc).__name__}: {str(exc)[:300]}\n\n"
                "The Office OS task was not changed. Please retry or use Telegram."
            )

    cur.execute("""
        UPDATE tasks
        SET status='COMPLETED',completed_at=CURRENT_TIMESTAMP
        WHERE id=%s
          AND LOWER(TRIM(COALESCE(assigned_to,'')))=LOWER(TRIM(%s))
          AND UPPER(COALESCE(status,'PENDING'))<>ALL(%s)
        RETURNING id,task,completed_at
    """, (int(task["id"]), staff["staff_name"], list(CLOSED)))
    completed = cur.fetchone()
    if not completed:
        conn.rollback()
        return "❌ The task could not be completed locally. Please refresh My Work."
    conn.commit()
    return (
        f"✅ Task #{completed['id']} marked completed.\n\n"
        f"📝 {completed.get('task') or task.get('task') or 'Work'}\n"
        f"👤 {staff['staff_name']}\n"
        f"🕒 {completed.get('completed_at')}"
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
        "Open the Office Menu below. You can also type CASE followed by a "
        "case number or title."
    )


def menu_rows() -> list[dict[str, str]]:
    return [
        {"id": "morning_dashboard", "title": "Morning Dashboard", "description": "Priorities and personal work brief"},
        {"id": "evening_dashboard", "title": "Evening Dashboard", "description": "Private day-closing board"},
        {"id": "today_hearings", "title": "Today Hearings", "description": "Today's cause list"},
        {"id": "tomorrow_hearings", "title": "Tomorrow Hearings", "description": "Tomorrow's cause list"},
        {"id": "my_work", "title": "My Work", "description": "View and complete assigned work"},
        {"id": "office_status", "title": "Office Status", "description": "Pending, overdue and attendance"},
        {"id": "attendance_status", "title": "Attendance Status", "description": "Your attendance today"},
        {"id": "check_in", "title": "Check In", "description": "Share current office location"},
        {"id": "check_out", "title": "Check Out", "description": "Share current office location"},
        {"id": "case_search", "title": "Case Search", "description": "Find case by number or title"},
    ]


def help_text() -> str:
    return (
        "ℹ️ WHATSAPP STAFF HELP\n\n"
        "Use MENU for the full office menu.\n"
        "Dashboards: MORNING DASHBOARD or EVENING DASHBOARD\n"
        "Hearings: TODAY HEARINGS or TOMORROW HEARINGS\n"
        "Work: MY WORK\n"
        "Complete: choose a task, or send DONE <task number>\n"
        "Search: CASE CS/123/2026 or CASE party name\n"
        "Attendance: CHECK IN, CHECK OUT or ATTENDANCE STATUS\n\n"
        "Every completion and attendance punch requires confirmation. Administrative "
        "corrections and approvals remain protected in Telegram."
    )


def handle_staff_inbound(item: dict[str, Any]) -> dict[str, Any]:
    """Route one persisted inbound message; returns staff reply controls."""
    ensure_whatsapp_staff_schema()
    phone = normalize_phone(str(item.get("phone") or ""))
    incoming = str(item.get("text") or "").strip()
    action_id = str(item.get("action_id") or "")
    completed_task = False
    attendance_success = False
    attendance_notification: str | None = None
    attendance_action: str | None = None
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            staff = _staff_for_phone(cur, phone)
            if not staff:
                return {"is_staff": False}
            if action_id.startswith("staff_attendance_confirm:"):
                action, argument = "ATTENDANCE_CONFIRM", action_id.partition(":")[2]
            elif action_id == "staff_attendance_cancel":
                action, argument = "ATTENDANCE_CANCEL", ""
            elif str(item.get("type") or "").lower() == "location":
                action, argument = "ATTENDANCE_LOCATION", ""
            elif action_id.startswith("staff_task_complete:"):
                action, argument = "TASK_COMPLETE", action_id.partition(":")[2]
            elif action_id.startswith("staff_task:"):
                action, argument = "TASK_SELECT", action_id.partition(":")[2]
            elif action_id == "staff_task_cancel":
                action, argument = "TASK_CANCEL", ""
            elif action_id == "morning_dashboard":
                action, argument = "MORNING_DASHBOARD", ""
            elif action_id == "evening_dashboard":
                action, argument = "EVENING_DASHBOARD", ""
            else:
                action, argument = classify_staff_command(incoming)
            menu = action == "MENU"
            replies: list[str]
            task_picker: list[dict[str, str]] = []
            task_confirm: int | None = None
            attendance_confirm: str | None = None
            if action == "MENU":
                reply = menu_text(staff["staff_name"])
                replies = [reply]
            elif action == "HELP":
                reply = help_text()
                replies = [reply]
            elif action == "MORNING_DASHBOARD":
                from services.whatsapp_dashboard_service import staff_morning_dashboard

                reply = staff_morning_dashboard(staff)
                replies = [reply]
            elif action == "EVENING_DASHBOARD":
                from services.whatsapp_dashboard_service import staff_evening_dashboard

                reply = staff_evening_dashboard(staff)
                replies = [reply]
            elif action == "MY_WORK":
                reply = _my_work(cur, staff["staff_name"])
                replies = [reply]
                task_picker = _task_picker_rows(cur, staff["staff_name"])
            elif action == "OFFICE_STATUS":
                reply = _office_status(cur, staff)
                replies = [reply]
            elif action == "ATTENDANCE_STATUS":
                reply = _attendance_status(cur, staff)
                replies = [reply]
            elif action == "TODAY_HEARINGS":
                replies = _hearing_replies(0)
                reply = replies[0]
            elif action == "TOMORROW_HEARINGS":
                replies = _hearing_replies(1)
                reply = replies[0]
            elif action == "CASE_PROMPT":
                reply = (
                    "🔎 Send CASE followed by the case number or party name.\n"
                    "Example: CASE CS/3848/2025"
                )
                replies = [reply]
            elif action == "CASE":
                reply = _case_lookup(cur, argument)
                replies = [reply]
            elif action == "ATTENDANCE_BEGIN":
                from services.whatsapp_attendance_service import begin_attendance

                reply = begin_attendance(phone, staff, argument)
                replies = [reply]
            elif action == "ATTENDANCE_LOCATION":
                from services.whatsapp_attendance_service import review_attendance_location

                result = review_attendance_location(
                    phone, staff,
                    latitude=item.get("latitude"),
                    longitude=item.get("longitude"),
                    message_timestamp=item.get("message_timestamp"),
                    forwarded=bool(item.get("forwarded")),
                )
                reply = result["reply"]
                attendance_confirm = result.get("confirm_action")
                replies = [reply]
            elif action == "ATTENDANCE_CONFIRM":
                from services.whatsapp_attendance_service import confirm_attendance

                result = confirm_attendance(phone, staff, argument)
                reply = result["reply"]
                attendance_success = bool(result.get("success"))
                attendance_notification = result.get("group_notification")
                attendance_action = argument if attendance_success else None
                replies = [reply]
            elif action == "ATTENDANCE_CANCEL":
                from services.whatsapp_attendance_service import cancel_attendance

                reply = cancel_attendance(phone)
                replies = [reply]
            elif action == "TASK_SELECT":
                task = _task_for_staff(cur, staff["staff_name"], argument)
                if not task:
                    reply = "❌ Task not found or it is not assigned to you. Send MY WORK to refresh."
                elif str(task.get("status") or "").upper() in CLOSED:
                    reply = f"ℹ️ Task #{task['id']} is already completed or closed."
                else:
                    reply = _task_confirmation(task)
                    task_confirm = int(task["id"])
                replies = [reply]
            elif action == "TASK_COMPLETE":
                reply = _complete_staff_task(cur, conn, staff, argument)
                replies = [reply]
                completed_task = reply.startswith("✅")
            elif action == "TASK_CANCEL":
                reply = "✅ Completion cancelled. No task was changed."
                replies = [reply]
            else:
                reply = menu_text(staff["staff_name"])
                replies = [reply]
                menu = True
    finally:
        conn.close()

    digest = hashlib.sha256(
        str(item.get("provider_message_id") or "").encode()
    ).digest()
    update_id = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    activity_id = record_staff_activity(
        update_id=update_id,
        event_kind=(
            "WHATSAPP_ATTENDANCE" if attendance_success
            else "WHATSAPP_TASK_COMPLETED" if completed_task
            else "WHATSAPP_MESSAGE"
        ),
        user_id=int(staff["telegram_user_id"]),
        staff_name=str(staff["staff_name"]),
        staff_role=str(staff.get("role") or "staff"),
        chat_id=None,
        chat_type="whatsapp_private",
        chat_title="WhatsApp Staff Companion",
        summary=(reply[:3000] if (completed_task or attendance_success) else incoming[:3000])
        or f"[{item.get('type') or 'message'}]",
        metadata={
            "phone": phone,
            "provider_message_id": item.get("provider_message_id"),
            "action_id": action_id or None,
        },
    )
    return {
        "is_staff": True, "staff": staff, "reply": reply, "replies": replies,
        "menu": menu, "menu_rows": menu_rows() if menu else [],
        "task_picker": task_picker, "task_confirm": task_confirm,
        "attendance_confirm": attendance_confirm,
        "attendance_notification": attendance_notification,
        "attendance_action": attendance_action,
        "activity_id": activity_id, "incoming": incoming, "phone": phone,
    }
