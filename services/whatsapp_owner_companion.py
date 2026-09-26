"""Private WhatsApp Owner Desk with controlled staff messaging."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.whatsapp_cloud import normalize_phone, send_text_message
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_owner_compose (
                    owner_phone TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('DIRECT','BROADCAST')),
                    recipient_telegram_id BIGINT,
                    recipient_name TEXT,
                    recipient_phone TEXT,
                    message_text TEXT,
                    stage TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_staff_direct_messages (
                    id BIGSERIAL PRIMARY KEY,
                    owner_phone TEXT NOT NULL,
                    recipient_telegram_id BIGINT,
                    recipient_name TEXT NOT NULL,
                    recipient_phone TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    provider_message_id TEXT,
                    provider_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ
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
            cur.execute("DELETE FROM whatsapp_owner_compose")
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
    if upper in {"MESSAGE", "MESSAGE STAFF", "SEND", "STAFF", "TEAM"}:
        return "MESSAGE", ""
    if upper == "BROADCAST":
        return "BROADCAST", ""
    if upper.startswith("BROADCAST "):
        return "BROADCAST", command[10:].strip()
    if upper in {"CANCEL", "STOP"}:
        return "CANCEL", ""
    if upper.startswith("CASE "):
        return "CASE", command[5:].strip()
    return "UNKNOWN", command


def owner_menu() -> str:
    return (
        "🏛 LAW OFFICE — OWNER DESK\n\n"
        "Welcome, Ajay. Choose a button or send:\n"
        "• OVERVIEW — office totals\n"
        "• MESSAGE — choose one staff member\n"
        "• @Name <message> — tag and message directly\n"
        "• BROADCAST <message> — all linked staff\n"
        "• ACTIVITY — recent staff actions\n"
        "• WORK — pending work across staff\n"
        "• CASE <number/title> — case search\n\n"
        "Every staff message requires confirmation. No paid template is sent automatically."
    )


def _office_work_rows(cur) -> list[dict[str, Any]]:
    cur.execute("""
        SELECT t.id AS task_id, t.task AS task_text,
               t.assigned_to AS staff_name, t.case_number AS case_number,
               t.deadline AS deadline, t.due_at AS due_at,
               COALESCE(linked_case.case_title,'') AS case_title
        FROM tasks t
        LEFT JOIN LATERAL (
            SELECT c.case_title
            FROM cases c
            WHERE LOWER(TRIM(COALESCE(c.case_number,'')))=
                  LOWER(TRIM(COALESCE(t.case_number,'')))
               OR LOWER(TRIM(COALESCE(c.case_id,'')))=
                  LOWER(TRIM(COALESCE(t.case_number,'')))
            ORDER BY c.id DESC
            LIMIT 1
        ) linked_case ON NULLIF(TRIM(COALESCE(t.case_number,'')),'') IS NOT NULL
        WHERE UPPER(COALESCE(t.status,'PENDING'))<>ALL(%s)
        ORDER BY t.id DESC
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
        ])
        if row.get("case_number"):
            lines.extend([
                f"⚖️ {row.get('case_title') or 'Case title not recorded'}",
                f"🔢 {row.get('case_number')}",
            ])
        else:
            lines.append("⚖️ General office work")
        lines.extend([
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


def _linked_staff(cur) -> list[dict[str, Any]]:
    cur.execute("""
        SELECT telegram_user_id,staff_name,whatsapp_phone
        FROM staff_accounts
        WHERE whatsapp_phone IS NOT NULL AND COALESCE(is_active,TRUE)=TRUE
        ORDER BY LOWER(staff_name)
    """)
    return [dict(row) for row in cur.fetchall()]


def match_tagged_staff(text: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str] | None:
    """Resolve @Exact Name using the longest linked name first."""
    incoming = re.sub(r"\s+", " ", str(text or "")).strip()
    if not incoming.startswith("@"):
        return None
    body = incoming[1:]
    for row in sorted(rows, key=lambda item: len(str(item.get("staff_name") or "")), reverse=True):
        name = str(row.get("staff_name") or "").strip()
        if name and body[:len(name)].casefold() == name.casefold():
            remainder = body[len(name):]
            if not remainder or remainder[0] in " :,-":
                message = remainder.lstrip(" :,-").strip()
                return row, message
    return None


def _save_compose(
    cur, owner_phone: str, scope: str, stage: str, *,
    recipient: dict[str, Any] | None = None, message: str | None = None,
) -> None:
    cur.execute("""
        INSERT INTO whatsapp_owner_compose(
            owner_phone,scope,recipient_telegram_id,recipient_name,
            recipient_phone,message_text,stage,updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT(owner_phone) DO UPDATE SET
            scope=EXCLUDED.scope,
            recipient_telegram_id=EXCLUDED.recipient_telegram_id,
            recipient_name=EXCLUDED.recipient_name,
            recipient_phone=EXCLUDED.recipient_phone,
            message_text=EXCLUDED.message_text,
            stage=EXCLUDED.stage,
            updated_at=NOW()
    """, (
        owner_phone, scope,
        recipient.get("telegram_user_id") if recipient else None,
        recipient.get("staff_name") if recipient else None,
        recipient.get("whatsapp_phone") if recipient else None,
        message, stage,
    ))


def _pending_compose(cur, owner_phone: str) -> dict[str, Any] | None:
    cur.execute("""
        SELECT * FROM whatsapp_owner_compose
        WHERE owner_phone=%s AND updated_at >= NOW() - INTERVAL '30 minutes'
    """, (owner_phone,))
    row = cur.fetchone()
    return dict(row) if row else None


def _clear_compose(cur, owner_phone: str) -> None:
    cur.execute("DELETE FROM whatsapp_owner_compose WHERE owner_phone=%s", (owner_phone,))


def _confirmation(scope: str, message: str, recipient_name: str | None = None) -> str:
    target = "all linked staff" if scope == "BROADCAST" else recipient_name or "staff member"
    return (
        f"⚠️ CONFIRM MESSAGE\n\nTo: {target}\n\n"
        f"{message[:3000]}\n\n"
        "Select Send Now or Cancel."
    )


def _staff_picker(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": f"owner_staff:{row['telegram_user_id']}",
            "title": str(row["staff_name"]),
            "description": f"Send privately to +{row['whatsapp_phone']}",
        }
        for row in rows[:10]
    ]


def _freeform_window_open(cur, phone: str) -> bool:
    cur.execute("""
        SELECT 1 FROM whatsapp_inbound_messages
        WHERE sender_phone=%s AND received_at >= NOW() - INTERVAL '24 hours'
        LIMIT 1
    """, (normalize_phone(phone),))
    return bool(cur.fetchone())


def _log_delivery(
    cur, owner_phone: str, recipient: dict[str, Any], message: str,
    scope: str, status: str, provider_id: str | None = None,
    error: str | None = None,
) -> None:
    cur.execute("""
        INSERT INTO whatsapp_staff_direct_messages(
            owner_phone,recipient_telegram_id,recipient_name,recipient_phone,
            message_text,scope,delivery_status,provider_message_id,
            provider_error,sent_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  CASE WHEN %s='SENT' THEN NOW() ELSE NULL END)
    """, (
        owner_phone, recipient.get("telegram_user_id"), recipient["staff_name"],
        recipient["whatsapp_phone"], message, scope, status, provider_id,
        (error or "")[:1000] or None, status,
    ))


def _deliver(cur, owner_phone: str, pending: dict[str, Any]) -> str:
    scope = str(pending["scope"])
    message = str(pending.get("message_text") or "").strip()
    if not message:
        return "❌ The draft was empty. Please start again with MESSAGE."
    if scope == "BROADCAST":
        recipients = _linked_staff(cur)
        prefix = "📢 OFFICE MESSAGE — AJAY CHAWLA\n\n"
    else:
        recipients = [{
            "telegram_user_id": pending.get("recipient_telegram_id"),
            "staff_name": pending.get("recipient_name"),
            "whatsapp_phone": pending.get("recipient_phone"),
        }]
        prefix = "📩 MESSAGE FROM AJAY CHAWLA\n\n"
    sent: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for recipient in recipients:
        name = str(recipient.get("staff_name") or "Staff")
        phone = str(recipient.get("whatsapp_phone") or "")
        if not phone or not _freeform_window_open(cur, phone):
            skipped.append(name)
            _log_delivery(
                cur, owner_phone, recipient, message, scope, "WINDOW_CLOSED",
                error="No inbound staff message within the last 24 hours.",
            )
            continue
        try:
            result = send_text_message(phone, (prefix + message)[:4096])
            sent.append(name)
            _log_delivery(
                cur, owner_phone, recipient, message, scope, "SENT",
                provider_id=result["provider_message_id"],
            )
        except Exception as exc:
            failed.append(name)
            _log_delivery(cur, owner_phone, recipient, message, scope, "FAILED", error=str(exc))
    lines = ["✅ STAFF MESSAGE RESULT", "", f"Sent: {len(sent)}"]
    if sent:
        lines.append("• " + ", ".join(sent))
    if skipped:
        lines.extend([
            "", f"Not sent (24-hour window closed): {len(skipped)}",
            "• " + ", ".join(skipped),
            "Ask them to send HI to the office bot, then send again.",
        ])
    if failed:
        lines.extend(["", f"Failed: {len(failed)}", "• " + ", ".join(failed)])
    lines.append("\nNo paid template was used.")
    return "\n".join(lines)[:4000]


def handle_owner_inbound(item: dict[str, Any]) -> dict[str, Any]:
    """Recognize the linked owner before staff/client routing."""
    if not staff_companion_enabled() or not _admin_id():
        return {"is_owner": False}
    phone = normalize_phone(str(item.get("phone") or ""))
    incoming = str(item.get("text") or "").strip()
    action_id = str(item.get("action_id") or "")
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

            if action_id == "owner_send_cancel":
                _clear_compose(cur, phone)
                conn.commit()
                return {"is_owner": True, "phone": phone, "reply": "✅ Message cancelled."}

            if action_id == "owner_send_confirm":
                pending = _pending_compose(cur, phone)
                if not pending or pending.get("stage") != "CONFIRM":
                    return {
                        "is_owner": True, "phone": phone,
                        "reply": "This draft expired. Send MESSAGE to start again.",
                    }
                _clear_compose(cur, phone)
                reply = _deliver(cur, phone, pending)
                conn.commit()
                return {"is_owner": True, "phone": phone, "reply": reply}

            if action_id.startswith("owner_staff:"):
                staff_id = action_id.partition(":")[2]
                cur.execute("""
                    SELECT telegram_user_id,staff_name,whatsapp_phone
                    FROM staff_accounts
                    WHERE telegram_user_id=%s AND whatsapp_phone IS NOT NULL
                      AND COALESCE(is_active,TRUE)=TRUE
                """, (staff_id,))
                recipient = cur.fetchone()
                if not recipient:
                    return {
                        "is_owner": True, "phone": phone,
                        "reply": "That staff link is no longer active. Send MESSAGE again.",
                    }
                _save_compose(cur, phone, "DIRECT", "AWAITING_TEXT", recipient=dict(recipient))
                conn.commit()
                return {
                    "is_owner": True, "phone": phone,
                    "reply": f"✍️ Selected {recipient['staff_name']}.\n\nType the private message now, or send CANCEL.",
                }

            action, argument = classify_owner_command(incoming)
            if action == "CANCEL":
                _clear_compose(cur, phone)
                conn.commit()
                return {"is_owner": True, "phone": phone, "reply": "✅ Message cancelled."}

            if action == "MENU":
                _clear_compose(cur, phone)
                conn.commit()
                return {"is_owner": True, "phone": phone, "reply": owner_menu(), "menu": True}

            rows = _linked_staff(cur) if action in {"MESSAGE", "BROADCAST", "UNKNOWN"} else []
            tagged = match_tagged_staff(incoming, rows) if action == "UNKNOWN" else None
            if tagged:
                recipient, message = tagged
                if not message:
                    _save_compose(cur, phone, "DIRECT", "AWAITING_TEXT", recipient=recipient)
                    conn.commit()
                    return {
                        "is_owner": True, "phone": phone,
                        "reply": f"✍️ Selected {recipient['staff_name']}.\n\nType the private message now, or send CANCEL.",
                    }
                _save_compose(
                    cur, phone, "DIRECT", "CONFIRM", recipient=recipient, message=message,
                )
                conn.commit()
                return {
                    "is_owner": True, "phone": phone,
                    "reply": _confirmation("DIRECT", message, str(recipient["staff_name"])),
                    "confirm": True,
                }

            if action == "MESSAGE":
                if not rows:
                    return {
                        "is_owner": True, "phone": phone,
                        "reply": "No active staff WhatsApp numbers are linked.",
                    }
                return {
                    "is_owner": True, "phone": phone,
                    "reply": "👥 Choose the staff member who should receive a private message.",
                    "staff_picker": _staff_picker(rows),
                }

            if action == "BROADCAST":
                if not argument:
                    return {
                        "is_owner": True, "phone": phone,
                        "reply": "Usage: BROADCAST <message>\n\nExample:\nBROADCAST Please attend the office meeting at 5 PM.",
                    }
                if not rows:
                    return {"is_owner": True, "phone": phone, "reply": "No linked staff recipients."}
                _save_compose(cur, phone, "BROADCAST", "CONFIRM", message=argument)
                conn.commit()
                return {
                    "is_owner": True, "phone": phone,
                    "reply": _confirmation("BROADCAST", argument), "confirm": True,
                }

            pending = _pending_compose(cur, phone)
            if action == "UNKNOWN" and pending and pending.get("stage") == "AWAITING_TEXT":
                if not incoming:
                    return {"is_owner": True, "phone": phone, "reply": "Please type the message."}
                recipient = {
                    "telegram_user_id": pending.get("recipient_telegram_id"),
                    "staff_name": pending.get("recipient_name"),
                    "whatsapp_phone": pending.get("recipient_phone"),
                }
                _save_compose(
                    cur, phone, "DIRECT", "CONFIRM", recipient=recipient, message=incoming,
                )
                conn.commit()
                return {
                    "is_owner": True, "phone": phone,
                    "reply": _confirmation("DIRECT", incoming, str(recipient["staff_name"])),
                    "confirm": True,
                }

            if action == "OVERVIEW":
                reply = _owner_overview(cur)
            elif action == "ACTIVITY":
                reply = _owner_activity(cur)
            elif action == "WORK":
                reply = _owner_work(cur)
            elif action == "CASE":
                reply = _case_lookup(cur, argument)
            else:
                names = ", ".join(str(row["staff_name"]) for row in rows)
                reply = (
                    "I could not identify that command.\n\n"
                    f"Linked staff: {names or 'None'}\n\n"
                    "Use MESSAGE, @Name <message>, BROADCAST <message>, or MENU."
                )
            return {"is_owner": True, "phone": phone, "reply": reply}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
