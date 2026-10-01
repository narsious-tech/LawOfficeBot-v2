"""Location-verified WhatsApp attendance using the existing Office OS tables."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

OFFICE_TZ = ZoneInfo("Asia/Kolkata")
PENDING_MINUTES = int(os.getenv("WHATSAPP_ATTENDANCE_PENDING_MINUTES", "10"))
DUPLICATE_MINUTES = int(os.getenv("ATTENDANCE_DUPLICATE_MINUTES", "10"))


def _now_local() -> datetime:
    return datetime.now(OFFICE_TZ).replace(tzinfo=None)


def build_attendance_notification(
    *,
    staff_name: str,
    action: str,
    office_name: str,
    distance_meters: float,
    event_time: datetime,
    map_link: str,
    working_minutes: int | None = None,
) -> str:
    """Build the Telegram office-group alert for a WhatsApp attendance punch."""
    action = str(action or "").upper()
    label = "CHECK-IN" if action == "CHECKIN" else "CHECK-OUT"
    icon = "🟢" if action == "CHECKIN" else "🔴"
    lines = [
        f"{icon} STAFF {label}",
        "",
        f"👤 Staff: {staff_name}",
        f"🏢 Office: {office_name}",
        "📍 Location recorded",
        f"📱 Source: WhatsApp",
        f"📏 Distance from office: {round(float(distance_meters))} metres",
        f"🕒 Time: {event_time.strftime('%d-%m-%Y %I:%M %p')}",
    ]
    if action == "CHECKOUT" and working_minutes is not None:
        lines.append(
            f"⏱ Working time: {working_minutes // 60}h {working_minutes % 60}m"
        )
    lines.append(f"🗺 Map: {map_link}")
    return "\n".join(lines)


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    first = math.radians(lat1)
    second = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(first) * math.cos(second) * math.sin(delta_lon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def ensure_whatsapp_attendance_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_attendance_pending (
                    whatsapp_phone TEXT PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    staff_name TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('CHECKIN','CHECKOUT')),
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    office_id INTEGER,
                    office_name TEXT,
                    distance_meters DOUBLE PRECISION,
                    stage TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()
    finally:
        conn.close()


def _session(cur, telegram_user_id: int, attendance_date) -> dict[str, Any] | None:
    cur.execute("""
        SELECT id,checkin_time,checkin_office_id,checkin_office_name,
               checkout_time,checkout_office_id,checkout_office_name,status
        FROM attendance_sessions
        WHERE telegram_user_id=%s AND attendance_date=%s
        ORDER BY id DESC LIMIT 1
    """, (telegram_user_id, attendance_date))
    row = cur.fetchone()
    return dict(row) if row else None


def _action_error(cur, telegram_user_id: int, action: str, now: datetime) -> str | None:
    session = _session(cur, telegram_user_id, now.date())
    if action == "CHECKIN" and session and session.get("status") == "OPEN" and not session.get("checkout_time"):
        return (
            "You are already checked in today.\n"
            f"Office: {session.get('checkin_office_name') or '-'}\n"
            f"Time: {session.get('checkin_time') or '-'}"
        )
    if action == "CHECKOUT":
        if not session:
            return "No check-in was found for today. Please check in first."
        if session.get("status") == "CLOSED" or session.get("checkout_time"):
            return "You have already checked out today."
    return None


def begin_attendance(phone: str, staff: dict[str, Any], action: str) -> str:
    action = str(action or "").upper()
    if action not in {"CHECKIN", "CHECKOUT"}:
        raise ValueError("Invalid attendance action.")
    ensure_whatsapp_attendance_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            error = _action_error(cur, int(staff["telegram_user_id"]), action, _now_local())
            if error:
                return "ℹ️ ATTENDANCE\n\n" + error
            cur.execute("""
                INSERT INTO whatsapp_attendance_pending(
                    whatsapp_phone,telegram_user_id,staff_name,action,stage,
                    created_at,updated_at
                ) VALUES (%s,%s,%s,%s,'AWAITING_LOCATION',NOW(),NOW())
                ON CONFLICT(whatsapp_phone) DO UPDATE SET
                    telegram_user_id=EXCLUDED.telegram_user_id,
                    staff_name=EXCLUDED.staff_name,
                    action=EXCLUDED.action,
                    latitude=NULL,longitude=NULL,office_id=NULL,office_name=NULL,
                    distance_meters=NULL,stage='AWAITING_LOCATION',
                    created_at=NOW(),updated_at=NOW()
            """, (phone, staff["telegram_user_id"], staff["staff_name"], action))
        conn.commit()
    finally:
        conn.close()
    label = "CHECK IN" if action == "CHECKIN" else "CHECK OUT"
    return (
        f"📍 {label} — LOCATION REQUIRED\n\n"
        "In WhatsApp, tap 📎/＋ → Location → Send your current location.\n\n"
        f"Send it within {PENDING_MINUTES} minutes. Forwarded locations are rejected. "
        "WhatsApp does not provide GPS-accuracy metres, so the bot verifies the "
        "approved-office radius and asks for confirmation before recording.\n\n"
        "Send CANCEL to stop."
    )


def _nearest_office(
    cur, latitude: float, longitude: float, action: str, office_scope: str = "ALL"
) -> dict[str, Any]:
    permission = "allow_checkin" if action == "CHECKIN" else "allow_checkout"
    scope = str(office_scope or "ALL").upper()
    scope_office = {
        "COURT_ONLY": "Court Chamber Office",
        "EVENING_ONLY": "Evening Office",
    }.get(scope)
    scope_sql = " AND LOWER(office_name)=LOWER(%s)" if scope_office else ""
    params = (scope_office,) if scope_office else ()
    cur.execute(f"""
        SELECT id,office_name,latitude,longitude,allowed_radius_meters
        FROM attendance_offices
        WHERE is_active=TRUE AND {permission}=TRUE
        {scope_sql}
        ORDER BY id
    """, params)
    nearest = None
    for row in cur.fetchall():
        candidate = dict(row)
        candidate["distance_meters"] = _distance_meters(
            latitude, longitude, float(candidate["latitude"]), float(candidate["longitude"])
        )
        if nearest is None or candidate["distance_meters"] < nearest["distance_meters"]:
            nearest = candidate
    if nearest is None:
        raise RuntimeError(f"No approved office is configured for {action.lower()}.")
    return nearest


def _fresh_timestamp(raw: Any) -> bool:
    try:
        stamp = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    age = datetime.now(timezone.utc) - stamp
    return timedelta(minutes=-1) <= age <= timedelta(minutes=PENDING_MINUTES)


def review_attendance_location(
    phone: str,
    staff: dict[str, Any],
    *,
    latitude: Any,
    longitude: Any,
    message_timestamp: Any,
    forwarded: bool,
) -> dict[str, Any]:
    ensure_whatsapp_attendance_schema()
    if forwarded:
        return {"reply": "❌ Forwarded locations cannot be used for attendance."}
    if not _fresh_timestamp(message_timestamp):
        return {"reply": "❌ This location is missing a fresh timestamp. Start Check In/Out again."}
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return {"reply": "❌ Valid location coordinates were not received."}

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM whatsapp_attendance_pending
                WHERE whatsapp_phone=%s AND telegram_user_id=%s
                  AND stage='AWAITING_LOCATION'
                  AND updated_at >= NOW() - (%s * INTERVAL '1 minute')
                FOR UPDATE
            """, (phone, staff["telegram_user_id"], PENDING_MINUTES))
            pending = cur.fetchone()
            if not pending:
                return {"reply": "❌ No active attendance request. Select Check In or Check Out first."}
            action = str(pending["action"])
            error = _action_error(cur, int(staff["telegram_user_id"]), action, _now_local())
            if error:
                cur.execute("DELETE FROM whatsapp_attendance_pending WHERE whatsapp_phone=%s", (phone,))
                conn.commit()
                return {"reply": "ℹ️ ATTENDANCE\n\n" + error}
            office = _nearest_office(
                cur, latitude, longitude, action,
                str(staff.get("attendance_office_scope") or "ALL"),
            )
            allowed = int(office.get("allowed_radius_meters") or 300)
            distance = float(office["distance_meters"])
            if distance > allowed:
                return {
                    "reply": (
                        "❌ OUTSIDE APPROVED ATTENDANCE AREA\n\n"
                        f"Nearest office: {office['office_name']}\n"
                        f"Distance: {round(distance)} metres\n"
                        f"Allowed radius: {allowed} metres\n\n"
                        "Move inside the approved area and share your current location again."
                    )
                }
            cur.execute("""
                UPDATE whatsapp_attendance_pending
                SET latitude=%s,longitude=%s,office_id=%s,office_name=%s,
                    distance_meters=%s,stage='CONFIRM',updated_at=NOW()
                WHERE whatsapp_phone=%s
            """, (
                latitude, longitude, office["id"], office["office_name"],
                distance, phone,
            ))
        conn.commit()
        label = "Check In" if action == "CHECKIN" else "Check Out"
        return {
            "reply": (
                f"⚠️ CONFIRM {label.upper()}\n\n"
                f"👤 {staff['staff_name']}\n"
                f"🏢 {office['office_name']}\n"
                f"📏 Distance: {round(distance)} metres\n"
                f"📍 https://www.google.com/maps?q={latitude},{longitude}\n\n"
                "Confirm only if this is your current location."
            ),
            "confirm_action": action,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_attendance(phone: str) -> str:
    ensure_whatsapp_attendance_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM whatsapp_attendance_pending WHERE whatsapp_phone=%s", (phone,))
        conn.commit()
    finally:
        conn.close()
    return "✅ Attendance action cancelled. Nothing was recorded."


def confirm_attendance(phone: str, staff: dict[str, Any], action: str) -> dict[str, Any]:
    action = str(action or "").upper()
    ensure_whatsapp_attendance_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM whatsapp_attendance_pending
                WHERE whatsapp_phone=%s AND telegram_user_id=%s AND action=%s
                  AND stage='CONFIRM'
                  AND updated_at >= NOW() - (%s * INTERVAL '1 minute')
                FOR UPDATE
            """, (phone, staff["telegram_user_id"], action, PENDING_MINUTES))
            pending = cur.fetchone()
            if not pending:
                return {"success": False, "reply": "❌ Attendance confirmation expired. Start again."}

            now = _now_local()
            error = _action_error(cur, int(staff["telegram_user_id"]), action, now)
            if error:
                cur.execute("DELETE FROM whatsapp_attendance_pending WHERE whatsapp_phone=%s", (phone,))
                conn.commit()
                return {"success": False, "reply": "ℹ️ ATTENDANCE\n\n" + error}

            cutoff = now - timedelta(minutes=DUPLICATE_MINUTES)
            cur.execute("""
                SELECT 1 FROM attendance_locations
                WHERE telegram_user_id=%s AND action=%s AND created_at>=%s
                LIMIT 1
            """, (staff["telegram_user_id"], action, cutoff))
            if cur.fetchone():
                return {
                    "success": False,
                    "reply": f"❌ This {action.lower()} was recorded recently. Wait {DUPLICATE_MINUTES} minutes.",
                }

            cur.execute("""
                SELECT ad_email,ad_password FROM staff_accounts
                WHERE telegram_user_id=%s AND whatsapp_phone=%s
                  AND COALESCE(is_active,TRUE)=TRUE LIMIT 1
            """, (staff["telegram_user_id"], phone))
            credentials = cur.fetchone()
            if not credentials or not credentials.get("ad_email") or not credentials.get("ad_password"):
                return {"success": False, "reply": "❌ Advocate Diaries credentials are unavailable."}

            from advocate_web import AdvocateWeb

            web = AdvocateWeb(
                email=str(credentials["ad_email"]).strip(),
                password=str(credentials["ad_password"]).strip(),
            )
            login_ok, _ = web.test_login()
            if not login_ok:
                return {"success": False, "reply": "❌ Advocate Diaries login failed. Nothing was recorded."}
            response = web.punch_in() if action == "CHECKIN" else web.punch_out()
            if response.status_code != 200:
                return {
                    "success": False,
                    "reply": f"❌ Advocate Diaries attendance failed (status {response.status_code}). Nothing was recorded.",
                }

            latitude = float(pending["latitude"])
            longitude = float(pending["longitude"])
            map_link = f"https://www.google.com/maps?q={latitude},{longitude}"
            cur.execute("""
                INSERT INTO attendance_locations(
                    staff_name,telegram_user_id,action,latitude,longitude,map_link,
                    office_id,office_name,distance_meters,accuracy_meters
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
            """, (
                staff["staff_name"], staff["telegram_user_id"], action,
                str(latitude), str(longitude), map_link, pending["office_id"],
                pending["office_name"], pending["distance_meters"],
            ))

            working_minutes = None
            if action == "CHECKIN":
                cur.execute("""
                    INSERT INTO attendance_sessions(
                        telegram_user_id,staff_name,attendance_date,checkin_time,
                        checkin_office_id,checkin_office_name,status,current_office_id,
                        current_office_name,created_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,'OPEN',%s,%s,%s,%s)
                    ON CONFLICT(telegram_user_id,attendance_date) DO UPDATE SET
                        staff_name=EXCLUDED.staff_name,checkin_time=EXCLUDED.checkin_time,
                        checkin_office_id=EXCLUDED.checkin_office_id,
                        checkin_office_name=EXCLUDED.checkin_office_name,
                        checkout_time=NULL,checkout_office_id=NULL,
                        checkout_office_name=NULL,status='OPEN',working_minutes=NULL,
                        current_office_id=EXCLUDED.current_office_id,
                        current_office_name=EXCLUDED.current_office_name,
                        updated_at=EXCLUDED.updated_at
                """, (
                    staff["telegram_user_id"], staff["staff_name"], now.date(), now,
                    pending["office_id"], pending["office_name"], pending["office_id"],
                    pending["office_name"], now, now,
                ))
            else:
                session = _session(cur, int(staff["telegram_user_id"]), now.date())
                checkin_time = session.get("checkin_time") if session else None
                if not checkin_time:
                    conn.rollback()
                    return {"success": False, "reply": "❌ Today's check-in time is unavailable."}
                working_minutes = max(0, int((now - checkin_time).total_seconds() // 60))
                cur.execute("""
                    UPDATE attendance_sessions SET
                        checkout_time=%s,checkout_office_id=%s,checkout_office_name=%s,
                        current_office_id=%s,current_office_name=%s,status='CLOSED',
                        working_minutes=%s,updated_at=%s
                    WHERE id=%s
                """, (
                    now, pending["office_id"], pending["office_name"],
                    pending["office_id"], pending["office_name"], working_minutes,
                    now, session["id"],
                ))
            cur.execute("DELETE FROM whatsapp_attendance_pending WHERE whatsapp_phone=%s", (phone,))
        conn.commit()
        label = "Check-in" if action == "CHECKIN" else "Check-out"
        lines = [
            f"✅ {label} completed successfully.", "",
            f"👤 {staff['staff_name']}", f"🏢 {pending['office_name']}",
            f"📏 Distance: {round(float(pending['distance_meters']))} metres",
            f"🕒 {now.strftime('%d-%m-%Y %I:%M %p')}", f"🗺 {map_link}",
        ]
        if working_minutes is not None:
            lines.append(f"⏱ Working time: {working_minutes // 60}h {working_minutes % 60}m")
        notification = build_attendance_notification(
            staff_name=str(staff["staff_name"]),
            action=action,
            office_name=str(pending["office_name"]),
            distance_meters=float(pending["distance_meters"]),
            event_time=now,
            map_link=map_link,
            working_minutes=working_minutes,
        )
        return {
            "success": True,
            "reply": "\n".join(lines),
            "group_notification": notification,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
