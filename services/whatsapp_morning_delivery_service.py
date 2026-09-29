"""Automatic, private WhatsApp morning brief delivery for linked staff."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_HOUR = 10
DEFAULT_MINUTE = 5
MAX_MESSAGE_LENGTH = 3900


def automatic_morning_enabled() -> bool:
    return os.getenv(
        "WHATSAPP_AUTOMATIC_MORNING_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}


def configured_morning_time() -> time:
    try:
        hour = int(os.getenv("WHATSAPP_MORNING_HOUR", str(DEFAULT_HOUR)))
        minute = int(os.getenv("WHATSAPP_MORNING_MINUTE", str(DEFAULT_MINUTE)))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        hour, minute = DEFAULT_HOUR, DEFAULT_MINUTE
    return time(hour=hour, minute=minute, tzinfo=IST)


def ensure_morning_delivery_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_staff_morning_delivery (
                    id BIGSERIAL PRIMARY KEY,
                    delivery_date DATE NOT NULL,
                    telegram_user_id BIGINT NOT NULL,
                    staff_name TEXT NOT NULL,
                    whatsapp_phone TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    provider_message_ids TEXT,
                    provider_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(delivery_date, telegram_user_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                whatsapp_staff_morning_delivery_status_idx
                ON whatsapp_staff_morning_delivery(
                    delivery_date DESC, delivery_status, telegram_user_id
                )
            """)
        conn.commit()
    finally:
        conn.close()


def split_whatsapp_message(message: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split at paragraph/line boundaries without dropping any text."""
    remaining = str(message or "").strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _whatsappize_brief(message: str) -> str:
    return str(message or "").replace(
        "Use /mytasks to view all pending tasks.",
        "Send MY WORK to view or complete pending work.",
    ).replace(
        "Use /mytasks to review or complete pending work.",
        "Send MY WORK to review or complete pending work.",
    )


def _today_hearing_summary(result: dict[str, Any], limit: int = 8) -> str:
    total = int(result.get("total") or 0)
    lines = [
        "⚖️ TODAY'S OFFICE HEARINGS",
        f"📅 {result['date'].strftime('%d-%m-%Y')}",
        f"📌 Total matters: {total}",
        f"🔗 Source: Advocate Diaries {result.get('source') or '-'}",
    ]
    if not total:
        lines.append("\nNo hearing is listed for today.")
        return "\n".join(lines)
    shown = 0
    for group in result.get("groups") or []:
        court = " · ".join(value for value in (
            str(group.get("court_name") or "Court"),
            str(group.get("judge_name") or ""),
            f"Floor {group.get('floor')}" if group.get("floor") else "",
            f"Room {group.get('room')}" if group.get("room") else "",
        ) if value)
        for case in group.get("cases") or []:
            if shown >= limit:
                break
            shown += 1
            lines.extend([
                "",
                f"{shown}. {case.get('case_title') or 'Title not recorded'}",
                f"   🔢 {case.get('case_number') or '-'}",
                f"   🏛 {court}",
                f"   📝 {case.get('stage') or 'Purpose not recorded'}",
            ])
        if shown >= limit:
            break
    if total > shown:
        lines.append(f"\n…and {total - shown} more. Send TODAY HEARINGS for the full list.")
    return "\n".join(lines)


def _attendance_line(cur, telegram_user_id: int, today) -> str:
    cur.execute("""
        SELECT checkin_time,checkout_time
        FROM attendance_sessions
        WHERE telegram_user_id=%s AND attendance_date=%s
        ORDER BY id DESC LIMIT 1
    """, (telegram_user_id, today))
    row = cur.fetchone()
    if not row or not row.get("checkin_time"):
        return "📍 Attendance: Not checked in — send CHECK IN on arrival."
    if row.get("checkout_time"):
        return "📍 Attendance: Checked out."
    return f"📍 Attendance: Checked in at {row['checkin_time'].strftime('%I:%M %p')}."


def _window_open(cur, phone: str) -> bool:
    from services.whatsapp_cloud import normalize_phone

    cur.execute("""
        SELECT 1 FROM whatsapp_inbound_messages
        WHERE sender_phone=%s AND received_at>=NOW()-INTERVAL '24 hours'
        LIMIT 1
    """, (normalize_phone(phone),))
    return bool(cur.fetchone())


def _save_result(
    cur,
    *,
    today,
    staff: dict[str, Any],
    trigger: str,
    status: str,
    provider_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    cur.execute("""
        INSERT INTO whatsapp_staff_morning_delivery(
            delivery_date,telegram_user_id,staff_name,whatsapp_phone,
            trigger_source,delivery_status,message_count,
            provider_message_ids,provider_error
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(delivery_date,telegram_user_id) DO UPDATE SET
            staff_name=EXCLUDED.staff_name,
            whatsapp_phone=EXCLUDED.whatsapp_phone,
            trigger_source=EXCLUDED.trigger_source,
            delivery_status=EXCLUDED.delivery_status,
            message_count=EXCLUDED.message_count,
            provider_message_ids=EXCLUDED.provider_message_ids,
            provider_error=EXCLUDED.provider_error,
            updated_at=NOW()
    """, (
        today, staff["telegram_user_id"], staff["staff_name"],
        staff["whatsapp_phone"], trigger, status, len(provider_ids or []),
        ",".join(provider_ids or []) or None, (error or "")[:1000] or None,
    ))


def deliver_automatic_morning(
    *,
    staff_name: str | None = None,
    telegram_user_id: int | None = None,
    force: bool = False,
    trigger: str = "SCHEDULED_1005",
) -> dict[str, Any]:
    """Deliver one private morning bundle per linked staff member."""
    from commands.dashboard import build_staff_morning_briefs
    from services.office_calendar_service import is_morning_office_open
    from services.staff_hearing_service import fetch_staff_hearings
    from services.whatsapp_cloud import send_text_message, transport_ready

    now = datetime.now(IST)
    today = now.date()
    result: dict[str, Any] = {
        "sent": [], "window_closed": [], "not_linked": [],
        "already_sent": [], "failed": [], "skipped": None,
    }
    if not force and not automatic_morning_enabled():
        result["skipped"] = "WHATSAPP_AUTOMATIC_MORNING_ENABLED is false"
        return result
    if not is_morning_office_open(today):
        result["skipped"] = "Morning office is closed"
        return result
    if not transport_ready():
        result["skipped"] = "WhatsApp transport is not ready"
        return result

    ensure_morning_delivery_schema()
    briefs = build_staff_morning_briefs()
    brief_by_id = {
        int(item["telegram_user_id"]): item
        for item in briefs if item.get("telegram_user_id")
    }
    try:
        hearing_summary = _today_hearing_summary(fetch_staff_hearings(today))
    except Exception as exc:
        hearing_summary = (
            "⚖️ TODAY'S OFFICE HEARINGS\n"
            f"Cause-list summary is temporarily unavailable ({type(exc).__name__}).\n"
            "Send TODAY HEARINGS to retry."
        )

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT telegram_user_id,staff_name,whatsapp_phone
                FROM staff_accounts
                WHERE COALESCE(is_active,TRUE)=TRUE
                  AND whatsapp_phone IS NOT NULL
            """
            params: list[Any] = []
            if telegram_user_id is not None:
                query += " AND telegram_user_id=%s"
                params.append(int(telegram_user_id))
            if staff_name:
                query += " AND LOWER(TRIM(staff_name))=LOWER(TRIM(%s))"
                params.append(staff_name)
            query += " ORDER BY LOWER(staff_name)"
            cur.execute(query, params)
            staff_rows = [dict(row) for row in cur.fetchall()]
            if (staff_name or telegram_user_id is not None) and not staff_rows:
                result["not_linked"].append(staff_name or str(telegram_user_id))

            for staff in staff_rows:
                name = str(staff["staff_name"])
                # Create a row before sending, then lock it. This prevents the
                # 10:05 job and a simultaneous check-in catch-up from both
                # delivering the same employee's brief.
                cur.execute("""
                    INSERT INTO whatsapp_staff_morning_delivery(
                        delivery_date,telegram_user_id,staff_name,whatsapp_phone,
                        trigger_source,delivery_status
                    ) VALUES (%s,%s,%s,%s,%s,'PROCESSING')
                    ON CONFLICT(delivery_date,telegram_user_id) DO NOTHING
                """, (
                    today, staff["telegram_user_id"], name,
                    staff["whatsapp_phone"], trigger,
                ))
                cur.execute("""
                    SELECT delivery_status FROM whatsapp_staff_morning_delivery
                    WHERE delivery_date=%s AND telegram_user_id=%s
                    FOR UPDATE
                """, (today, staff["telegram_user_id"]))
                prior = cur.fetchone()
                if not force and prior and prior.get("delivery_status") == "SENT":
                    result["already_sent"].append(name)
                    continue
                if not _window_open(cur, str(staff["whatsapp_phone"])):
                    _save_result(
                        cur, today=today, staff=staff, trigger=trigger,
                        status="WINDOW_CLOSED",
                        error="No inbound staff WhatsApp message within 24 hours",
                    )
                    result["window_closed"].append(name)
                    continue

                brief = brief_by_id.get(int(staff["telegram_user_id"]))
                work_message = _whatsappize_brief(
                    str((brief or {}).get("message") or (
                        "🌅 YOUR MORNING WORK BRIEF\n"
                        f"👤 {name.upper()}\n\n"
                        "No personal work data is available. Send MY WORK to refresh."
                    ))
                )
                attendance = _attendance_line(cur, int(staff["telegram_user_id"]), today)
                bundle = (
                    work_message
                    + "\n\n──────────\n\n"
                    + hearing_summary
                    + "\n\n──────────\n"
                    + attendance
                    + "\n📲 Send MENU for all office controls."
                )
                chunks = split_whatsapp_message(bundle)
                provider_ids: list[str] = []
                try:
                    for chunk in chunks:
                        sent = send_text_message(str(staff["whatsapp_phone"]), chunk)
                        provider_ids.append(str(sent["provider_message_id"]))
                    _save_result(
                        cur, today=today, staff=staff, trigger=trigger,
                        status="SENT", provider_ids=provider_ids,
                    )
                    result["sent"].append(name)
                except Exception as exc:
                    _save_result(
                        cur, today=today, staff=staff, trigger=trigger,
                        status="FAILED", provider_ids=provider_ids,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    result["failed"].append({"staff": name, "error": str(exc)[:300]})
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def deliver_checkin_catchup(staff: dict[str, Any]) -> dict[str, Any]:
    """Catch up after a late check-in, but never before the scheduled time."""
    if not automatic_morning_enabled():
        return {"skipped": "WHATSAPP_AUTOMATIC_MORNING_ENABLED is false"}
    now = datetime.now(IST)
    scheduled = configured_morning_time()
    if (now.hour, now.minute) < (scheduled.hour, scheduled.minute):
        return {"skipped": "Scheduled morning time has not arrived"}
    return deliver_automatic_morning(
        telegram_user_id=int(staff["telegram_user_id"]),
        trigger="CHECKIN_CATCHUP",
    )


async def whatsapp_staff_morning_job(context) -> None:
    result = await asyncio.to_thread(deliver_automatic_morning)
    print(
        "WHATSAPP STAFF MORNING COMPLETED: "
        f"sent={len(result.get('sent') or [])}, "
        f"window_closed={len(result.get('window_closed') or [])}, "
        f"already_sent={len(result.get('already_sent') or [])}, "
        f"failed={len(result.get('failed') or [])}, "
        f"skipped={result.get('skipped') or '-'}"
    )
