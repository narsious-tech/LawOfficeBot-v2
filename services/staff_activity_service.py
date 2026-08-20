"""Persistent, privacy-aware feed of staff interactions with the Telegram bot."""
from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import DATABASE_URL

IST = ZoneInfo("Asia/Kolkata")
SENSITIVE_COMMANDS = {"linkstaff"}
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def activity_feed_enabled() -> bool:
    return os.getenv("STAFF_ACTIVITY_FEED_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def admin_activity_chat_id() -> int | None:
    # Always notify Ajay privately. Never fall back to ADMIN_CHAT_ID because it
    # may be the office group and would disclose staff activity to everyone.
    raw = os.getenv("ADMIN_USER_ID", "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def ensure_staff_activity_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    import psycopg2
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_bot_activity (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_update_id BIGINT,
                    event_kind TEXT NOT NULL,
                    telegram_user_id BIGINT NOT NULL,
                    staff_name TEXT NOT NULL,
                    staff_role TEXT,
                    chat_id BIGINT,
                    chat_type TEXT,
                    chat_title TEXT,
                    summary TEXT NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    notified_at TIMESTAMPTZ,
                    notification_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(telegram_update_id, event_kind)
                )
                """)
                cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_staff_bot_activity_created
                ON staff_bot_activity(created_at DESC, id DESC)
                """)
                cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_staff_bot_activity_staff
                ON staff_bot_activity(telegram_user_id, created_at DESC)
                """)
            conn.commit()
            _SCHEMA_READY = True
        finally:
            conn.close()


def redact_message_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    first = text.split(maxsplit=1)[0].lower().lstrip("/").split("@", 1)[0]
    if first in SENSITIVE_COMMANDS:
        return f"/{first} [CREDENTIALS REDACTED]"
    return text[:3000]


def message_summary(message) -> tuple[str, dict[str, Any]]:
    text = redact_message_text(getattr(message, "text", None))
    caption = redact_message_text(getattr(message, "caption", None))
    if text:
        kind = "COMMAND" if text.startswith("/") else "TEXT"
        return text, {"message_kind": kind}
    document = getattr(message, "document", None)
    if document:
        name = getattr(document, "file_name", None) or "Unnamed document"
        return f"📎 Document: {name}", {
            "message_kind": "DOCUMENT",
            "file_name": name,
            "mime_type": getattr(document, "mime_type", None),
            "file_size": getattr(document, "file_size", None),
            "caption": caption,
        }
    photos = getattr(message, "photo", None) or []
    if photos:
        return "🖼 Photo" + (f" — {caption}" if caption else ""), {
            "message_kind": "PHOTO", "caption": caption
        }
    location = getattr(message, "location", None)
    if location:
        return (
            f"📍 Location: {location.latitude:.6f}, {location.longitude:.6f}",
            {"message_kind": "LOCATION"},
        )
    contact = getattr(message, "contact", None)
    if contact:
        label = " ".join(filter(None, (
            getattr(contact, "first_name", None),
            getattr(contact, "last_name", None),
        ))) or "Contact"
        return f"👤 Contact shared: {label}", {"message_kind": "CONTACT"}
    voice = getattr(message, "voice", None)
    if voice:
        return "🎙 Voice message", {"message_kind": "VOICE"}
    audio = getattr(message, "audio", None)
    if audio:
        return "🎵 Audio message", {"message_kind": "AUDIO"}
    video = getattr(message, "video", None)
    if video:
        return "🎥 Video" + (f" — {caption}" if caption else ""), {
            "message_kind": "VIDEO", "caption": caption
        }
    return "📨 Telegram update", {"message_kind": "OTHER"}


def record_staff_activity(
    *, update_id: int | None, event_kind: str, user_id: int,
    staff_name: str, staff_role: str, chat_id: int | None,
    chat_type: str | None, chat_title: str | None, summary: str,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    import psycopg2
    from psycopg2.extras import Json
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO staff_bot_activity (
                    telegram_update_id,event_kind,telegram_user_id,staff_name,
                    staff_role,chat_id,chat_type,chat_title,summary,metadata_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (telegram_update_id,event_kind) DO NOTHING
                RETURNING id
            """, (
                update_id,event_kind,user_id,staff_name,staff_role,chat_id,
                chat_type,chat_title,summary,Json(metadata or {}),
            ))
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else None
    finally:
        conn.close()


def mark_activity_notification(
    activity_id: int, *, error: str | None = None
) -> None:
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE staff_bot_activity
                SET notified_at=CASE WHEN %s IS NULL THEN NOW() ELSE notified_at END,
                    notification_error=%s
                WHERE id=%s
            """, (error, (error or "")[:1000] or None, activity_id))
        conn.commit()
    finally:
        conn.close()


def recent_staff_activity(limit: int = 25) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id,event_kind,staff_name,staff_role,chat_type,chat_title,
                       summary,notified_at,notification_error,created_at
                FROM staff_bot_activity
                ORDER BY created_at DESC,id DESC LIMIT %s
            """, (max(1, min(int(limit), 100)),))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def render_admin_notification(
    *, staff_name: str, staff_role: str, event_kind: str,
    chat_type: str | None, chat_title: str | None, summary: str,
) -> str:
    now = datetime.now(IST).strftime("%d-%m-%Y %I:%M:%S %p")
    place = chat_title or ("Private bot chat" if chat_type == "private" else chat_type or "-")
    return (
        "🔔 STAFF BOT ACTIVITY\n\n"
        f"👤 {staff_name}\n"
        f"🎭 Role: {staff_role.title()}\n"
        f"📍 Chat: {place}\n"
        f"🧩 Activity: {event_kind.replace('_', ' ').title()}\n"
        f"🕒 {now}\n\n"
        f"💬 {summary[:3000]}"
    )
