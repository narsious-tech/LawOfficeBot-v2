"""WhatsApp PDF intake for the restricted judge-leave workflow."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.judge_leave_service import (
    create_leave_batch,
    decide_batch,
    extract_pdf_text,
    match_leave_groups,
)
from services.office_calendar_service import manual_evening_plan
from services.whatsapp_cloud import download_media, normalize_phone

IST = ZoneInfo("Asia/Kolkata")
AUTHORIZED = {"priya", "preet", "isha"}


def _identity(phone: str) -> dict | None:
    phone = normalize_phone(phone)
    from services.whatsapp_owner_companion import ensure_owner_schema

    ensure_owner_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 'OWNER' AS kind,'Ajay' AS name,owner_telegram_id AS telegram_id
                FROM whatsapp_owner_link WHERE id=1 AND whatsapp_phone=%s
            """, (phone,))
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("""
                SELECT 'STAFF' AS kind,staff_name AS name,telegram_user_id AS telegram_id
                FROM staff_accounts
                WHERE whatsapp_phone=%s AND COALESCE(is_active,TRUE)=TRUE LIMIT 1
            """, (phone,))
            row = cur.fetchone()
            if not row:
                return None
            result = dict(row)
            first = str(result.get("name") or "").strip().casefold().split(" ", 1)[0]
            return result if first in AUTHORIZED else None
    except psycopg2.Error:
        return None
    finally:
        conn.close()


def _preview(batch: dict) -> str:
    lines = [
        "⚠️ CONFIRM JUDGE LEAVE PDF",
        f"📅 {batch['target_date']:%d-%m-%Y}",
    ]
    if not batch.get("date_mentioned"):
        lines.append("🟠 The selected date was not clearly found in the PDF text.")
    total = 0
    for match in batch.get("matches") or []:
        count = int(match.get("case_count") or 0)
        total += count
        lines.append(f"• {match.get('judge_name') or '-'} · {count} matter(s)")
    lines.extend([
        "",
        f"Files to ignore after confirmation: {total}",
        "No case date or Advocate Diaries record will be changed.",
    ])
    return "\n".join(lines)[:1024]


def handle_judge_leave_inbound(item: dict):
    kind = str(item.get("type") or "").casefold()
    action = str(item.get("action_id") or "")
    if kind != "document" and not action.startswith("judge_leave_"):
        return None
    phone = normalize_phone(item.get("phone") or "")
    identity = _identity(phone)
    if not identity:
        return None
    source_identity = f"whatsapp:{phone}"
    if action.startswith("judge_leave_confirm:") or action.startswith("judge_leave_cancel:"):
        confirm = action.startswith("judge_leave_confirm:")
        try:
            batch_id = int(action.rsplit(":", 1)[1])
            result = decide_batch(batch_id, source_identity, confirm)
            if not confirm:
                reply = "✅ Judge-leave import cancelled. No matter was excluded."
            else:
                count = sum(int(row.get("case_count") or 0) for row in result.get("entries") or [])
                reply = (
                    "✅ JUDGE LEAVE CONFIRMED\n\n"
                    f"📅 {result['target_date']:%d-%m-%Y}\n"
                    f"👨‍⚖️ Judges: {len(result.get('entries') or [])}\n"
                    f"📁 Matters excluded: {count}\n\n"
                    "The 30-minute-prior alert will run automatically."
                )
        except Exception as exc:
            reply = f"❌ Judge-leave decision failed safely:\n{exc}"
        return {"phone": phone, "reply": reply, "buttons": []}
    media_id = str(item.get("media_id") or "")
    mime = str(item.get("media_mime_type") or "").casefold()
    filename = str(item.get("media_filename") or "judge-leave.pdf")
    if not media_id or ("pdf" not in mime and not filename.casefold().endswith(".pdf")):
        return {"phone": phone, "reply": "❌ Please send the official judge-leave PDF.", "buttons": []}
    try:
        data, downloaded_mime = download_media(media_id)
        if "pdf" not in downloaded_mime.casefold() and not data.startswith(b"%PDF"):
            raise ValueError("The WhatsApp document is not a PDF.")
        text = extract_pdf_text(data)
        target = manual_evening_plan(datetime.now(IST).date()).target_date
        from commands.dashboard import fetch_advocate_diaries_cause_groups

        groups, _ = fetch_advocate_diaries_cause_groups(target)
        matches = match_leave_groups(text, groups)
        batch = create_leave_batch(
            target_date=target,
            pdf_bytes=data,
            filename=filename,
            pdf_text=text,
            matches=matches,
            source_channel="WHATSAPP",
            source_identity=source_identity,
            actor_name=str(identity.get("name") or "Authorized user"),
            actor_telegram_id=identity.get("telegram_id"),
        )
        return {
            "phone": phone,
            "reply": _preview(batch),
            "buttons": [
                (f"judge_leave_confirm:{batch['id']}", "Confirm"),
                (f"judge_leave_cancel:{batch['id']}", "Cancel"),
            ],
        }
    except Exception as exc:
        return {"phone": phone, "reply": f"❌ Judge-leave PDF could not be prepared safely:\n{exc}", "buttons": []}
