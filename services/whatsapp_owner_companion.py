"""Private, read-only WhatsApp view for the office owner."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.whatsapp_cloud import normalize_phone
from services.whatsapp_staff_companion import (
    CLOSED, OFFICE_TZ, _case_lookup, _deadline_date, staff_companion_enabled,
)


def _admin_id() -> int | None:
    raw = os.getenv("ADMIN_USER_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def ensure_owner_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_owner_link (
                    id SMALLINT PRIMARY KEY CHECK (id=1),
                    whatsapp_phone TEXT UNIQUE NOT NULL,
                    owner_telegram_id BIGINT NOT NULL,
                    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()
    finally:
        conn.close()


def link_owner_phone(phone: str, admin_id: int) -> str:
    if not _admin_id() or admin_id != _admin_id():
        raise PermissionError("Only the configured office owner can link this number.")
    normalized = normalize_phone(phone)
    ensure_owner_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT staff_name FROM staff_accounts WHERE whatsapp_phone=%s LIMIT 1",
                (normalized,),
            )
            staff = cur.fetchone()
            if staff:
                raise ValueError(
                    f"This number is already linked to staff member {staff['staff_name']}."
                )
            cur.execute("""
                INSERT INTO whatsapp_owner_link (id,whatsapp_phone,owner_telegram_id)
                VALUES (1,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    whatsapp_phone=EXCLUDED.whatsapp_phone,
                    owner_telegram_id=EXCLUDED.owner_telegram_id,
                    linked_at=NOW()
            """, (normalized, admin_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return normalized


def linked_owner_phone() -> str | None:
    if not _admin_id():
        return None
    ensure_owner_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT whatsapp_phone FROM whatsapp_owner_link "
                "WHERE id=1 AND owner_telegram_id=%s", (_admin_id(),)
            )
            row = cur.fetchone()
            return str(row["whatsapp_phone"]) if row else None
    finally:
        conn.close()


def unlink_owner_phone(admin_id: int) -> None:
    if not _admin_id() or admin_id != _admin_id():
        raise PermissionError("Only the configured office owner can unlink this number.")
    ensure_owner_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM whatsapp_owner_link WHERE id=1")
        conn.commit()
    finally:
        conn.close()


def classify_owner_command(text: str) -> tuple[str, str]:
    command = re.sub(r"\s+", " ", str(text or "")).strip()
    upper = command.upper()
    if upper in {"HI", "HELLO", "MENU", "START", "HELP", "/START"}:
        return "MENU", ""
    if upper in {"OVERVIEW", "OFFICE STATUS", "STATUS"}:
        return "OVERVIEW", ""
    if upper in {"ACTIVITY", "STAFF ACTIVITY", "RECENT ACTIVITY"}:
        return "ACTIVITY", ""
    if upper in {"WORK", "PENDING WORK", "TASKS", "ALL WORK"}:
        return "WORK", ""
    if upper.startswith("CASE "):
        return "CASE", command[5:].strip()
    return "MENU", ""


def owner_menu() -> str:
    return (
        "🏛 LAW OFFICE — OWNER DESK\n\n"
        "Welcome, Ajay. Choose a button or send:\n"
        "• OVERVIEW — office totals\n"
        "• ACTIVITY — recent staff actions\n"
        "• WORK — pending work across staff\n"
        "• CASE <number/title> — case search\n\n"
        "Administrative changes remain in your private Telegram bot."
    )


def _office_work_rows(cur) -> list[dict[str, Any]]:
    cur.execute("""
        SELECT id AS task_id, task AS task_text, assigned_to AS staff_name,
               case_number AS case_number, deadline AS deadline,
               due_at AS due_at
        FROM tasks
        WHERE UPPER(COALESCE(status,'PENDING'))<>ALL(%s)
        ORDER BY id DESC
    """, (list(CLOSED),))
    return list(cur.fetchall())


def _owner_overview(cur) -> str:
    rows = _office_work_rows(cur)
    today = datetime.now(OFFICE_TZ).date()
    overdue = 0
    staff_counts: dict[str, int] = {}
    for row in rows:
        due = _deadline_date(row.get("due_at") or row.get("deadline"))
        if due and due < today:
            overdue += 1
        staff_name = str(row.get("staff_name") or "Unassigned").strip() or "Unassigned"
        staff_counts[staff_name] = staff_counts.get(staff_name, 0) + 1
    lines = [
        "🏢 OFFICE OVERVIEW", "", f"📋 Pending work: {len(rows)}",
        f"🔴 Overdue: {overdue}", "", "By assignee:",
    ]
    lines.extend(
        f"• {name}: {count}" for name, count in
        sorted(staff_counts.items(), key=lambda entry: (-entry[1], entry[0].lower()))[:12]
    )
    if not staff_counts:
        lines.append("No pending work.")
    lines.append("\nSend WORK for task details.")
    return "\n".join(lines)[:4000]


def _owner_work(cur) -> str:
    rows = _office_work_rows(cur)
    if not rows:
        return "✅ OFFICE WORK\n\nNo pending work."
    today = datetime.now(OFFICE_TZ).date()
    def priority(row):
        due = _deadline_date(row.get("due_at") or row.get("deadline"))
        return (0 if due and due < today else 1, due or date.max, row.get("task_id") or 0)
    lines = ["📋 PENDING OFFICE WORK", ""]
    for row in sorted(rows, key=priority)[:10]:
        lines.extend([
            f"#{row['task_id']} · {row.get('staff_name') or 'Unassigned'}",
            f"📝 {row.get('task_text') or 'No description'}",
            f"⚖️ {row.get('case_number') or 'General office work'}",
            f"📅 Due: {row.get('due_at') or row.get('deadline') or 'Not fixed'}", "",
        ])
    lines.append(f"Showing {min(len(rows), 10)} of {len(rows)}. Use Telegram for updates.")
    return "\n".join(lines)[:4000]


def _owner_activity(cur) -> str:
    cur.execute("""
        SELECT staff_name,summary,created_at FROM staff_bot_activity
        WHERE staff_role IN ('staff','supervisor')
        ORDER BY created_at DESC,id DESC LIMIT 6
    """)
    rows = cur.fetchall()
    if not rows:
        return "🔔 STAFF ACTIVITY\n\nNo staff activity recorded yet."
    lines = ["🔔 RECENT STAFF ACTIVITY", ""]
    for row in rows:
        stamp = row["created_at"]
        if isinstance(stamp, datetime):
            stamp = stamp.astimezone(OFFICE_TZ).strftime("%d-%m %I:%M %p")
        lines.append(f"• {row['staff_name']} · {stamp}\n  {str(row.get('summary') or '')[:180]}")
    return "\n\n".join(lines)[:4000]


def handle_owner_inbound(item: dict[str, Any]) -> dict[str, Any]:
    """Recognize the Telegram-linked owner number before staff/client routing."""
    if not staff_companion_enabled() or not _admin_id():
        return {"is_owner": False}
    phone = normalize_phone(str(item.get("phone") or ""))
    ensure_owner_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 1 FROM whatsapp_owner_link
                WHERE id=1 AND whatsapp_phone=%s AND owner_telegram_id=%s
            """, (phone, _admin_id()))
            if not cur.fetchone():
                return {"is_owner": False}
            action, argument = classify_owner_command(item.get("text") or "")
            if action == "OVERVIEW":
                reply = _owner_overview(cur)
            elif action == "ACTIVITY":
                reply = _owner_activity(cur)
            elif action == "WORK":
                reply = _owner_work(cur)
            elif action == "CASE":
                reply = _case_lookup(cur, argument)
            else:
                reply = owner_menu()
            return {
                "is_owner": True, "phone": phone, "reply": reply,
                "menu": action == "MENU",
            }
    finally:
        conn.close()
