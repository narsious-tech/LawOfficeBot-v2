"""Privacy-safe WhatsApp reception desk for clients and new enquiries."""
from __future__ import annotations

import os
import re
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.whatsapp_cloud import normalize_phone


MENU_COMMANDS = {"HI", "HELLO", "MENU", "START", "HELP", "/START"}
STAGES = {"ENQUIRY_DETAILS", "APPOINTMENT_DETAILS", "EXISTING_CLIENT_DETAILS"}


def client_reception_enabled() -> bool:
    return os.getenv("WHATSAPP_CLIENT_RECEPTION_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def ensure_client_reception_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_client_reception_state (
                    sender_phone TEXT PRIMARY KEY,
                    sender_name TEXT,
                    stage TEXT NOT NULL,
                    related_case_id TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_client_requests (
                    id BIGSERIAL PRIMARY KEY,
                    sender_phone TEXT NOT NULL,
                    sender_name TEXT,
                    request_type TEXT NOT NULL,
                    request_text TEXT,
                    related_case_id TEXT,
                    provider_message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'NEW',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    reviewed_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                whatsapp_client_requests_provider_uidx
                ON whatsapp_client_requests(provider_message_id)
                WHERE provider_message_id IS NOT NULL
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS whatsapp_client_requests_status_idx
                ON whatsapp_client_requests(status,created_at DESC)
            """)
        conn.commit()
    finally:
        conn.close()


def reception_menu_rows() -> list[dict[str, str]]:
    return [
        {"id": "client_existing", "title": "Existing Client", "description": "My registered case"},
        {"id": "client_enquiry", "title": "New Legal Enquiry", "description": "Send enquiry to the office"},
        {"id": "client_appointment", "title": "Book Appointment", "description": "Request a suitable date and time"},
        {"id": "client_case_status", "title": "Case / Hearing Info", "description": "Registered clients only"},
        {"id": "client_documents", "title": "Documents Required", "description": "Ask the office for a checklist"},
        {"id": "client_location", "title": "Location & Timings", "description": "Court and evening office"},
        {"id": "client_contact", "title": "Contact the Office", "description": "Phone, email and assistance"},
    ]


def reception_menu() -> str:
    office = os.getenv("OFFICE_NAME", "Law Office of Ajay Chawla").strip()
    return (
        f"Welcome to {office}.\n\n"
        "Please choose an option below. Case information is shown only when this "
        "WhatsApp number is registered with the office. This service does not provide "
        "automated legal advice."
    )


def classify_client_command(text: str, action_id: str | None = None) -> tuple[str, str]:
    action = str(action_id or "").strip()
    if action.startswith("client_case:"):
        return "CASE_SELECTED", action.split(":", 1)[1]
    mapping = {
        "client_existing": "EXISTING",
        "client_enquiry": "ENQUIRY",
        "client_appointment": "APPOINTMENT",
        "client_case_status": "CASE_STATUS",
        "client_documents": "DOCUMENTS",
        "client_location": "LOCATION",
        "client_contact": "CONTACT",
        "client_menu": "MENU",
        "client_cancel": "CANCEL",
    }
    if action in mapping:
        return mapping[action], ""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    upper = value.upper()
    if upper in MENU_COMMANDS:
        return "MENU", ""
    if upper in {"EXISTING CLIENT", "MY CASE", "MY CASES"}:
        return "EXISTING", ""
    if upper in {"NEW LEGAL ENQUIRY", "NEW ENQUIRY", "ENQUIRY"}:
        return "ENQUIRY", ""
    if upper in {"BOOK APPOINTMENT", "APPOINTMENT"}:
        return "APPOINTMENT", ""
    if upper in {"CASE", "CASE STATUS", "HEARING", "CASE / HEARING INFO"}:
        return "CASE_STATUS", ""
    if upper in {"DOCUMENTS", "DOCUMENTS REQUIRED"}:
        return "DOCUMENTS", ""
    if upper in {"LOCATION", "TIMINGS", "LOCATION & TIMINGS"}:
        return "LOCATION", ""
    if upper in {"CONTACT", "CONTACT OFFICE", "CONTACT THE OFFICE"}:
        return "CONTACT", ""
    if upper in {"CANCEL", "STOP"}:
        return "CANCEL", ""
    return "MESSAGE", value


def _registered_cases(cur, phone: str) -> list[dict[str, Any]]:
    normalized = normalize_phone(phone)
    short = normalized[-10:]
    cur.execute("""
        SELECT DISTINCT ON (c.id)
            c.id,
            COALESCE(NULLIF(TRIM(c.case_number),''),NULLIF(TRIM(c.case_id),'')) AS case_number,
            COALESCE(NULLIF(TRIM(c.case_title),''),'Case title not recorded') AS case_title,
            COALESCE(c.next_hearing,c.hearing_date) AS next_hearing,
            COALESCE(NULLIF(TRIM(c.status),''),'Status not recorded') AS status
        FROM cases c
        LEFT JOIN client_contacts cc
          ON LOWER(TRIM(cc.case_id)) IN (
              LOWER(TRIM(COALESCE(c.case_id,''))),
              LOWER(TRIM(COALESCE(c.case_number,'')))
          )
        WHERE REGEXP_REPLACE(COALESCE(c.mobile,''),'[^0-9]','','g') IN (%s,%s)
           OR REGEXP_REPLACE(COALESCE(cc.whatsapp_number,''),'[^0-9]','','g') IN (%s,%s)
        ORDER BY c.id DESC
        LIMIT 10
    """, (normalized, short, normalized, short))
    return [dict(row) for row in cur.fetchall()]


def _case_rows(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for case in cases:
        number = str(case.get("case_number") or "Case number not recorded")
        title = str(case.get("case_title") or "Case title not recorded")
        rows.append({
            "id": f"client_case:{case['id']}",
            "title": number[:24],
            "description": title[:72],
        })
    return rows


def _selected_registered_case(cur, phone: str, case_row_id: str) -> dict[str, Any] | None:
    if not str(case_row_id).isdigit():
        return None
    wanted = int(case_row_id)
    for case in _registered_cases(cur, phone):
        if int(case["id"]) == wanted:
            return case
    return None


def _save_state(cur, phone: str, name: str, stage: str, case_id: str | None = None) -> None:
    if stage not in STAGES:
        raise ValueError("Invalid client reception state.")
    cur.execute("""
        INSERT INTO whatsapp_client_reception_state
            (sender_phone,sender_name,stage,related_case_id,updated_at)
        VALUES (%s,%s,%s,%s,NOW())
        ON CONFLICT (sender_phone) DO UPDATE SET
            sender_name=EXCLUDED.sender_name,
            stage=EXCLUDED.stage,
            related_case_id=EXCLUDED.related_case_id,
            updated_at=NOW()
    """, (phone, name, stage, case_id))


def _clear_state(cur, phone: str) -> None:
    cur.execute("DELETE FROM whatsapp_client_reception_state WHERE sender_phone=%s", (phone,))


def _record_request(
    cur, *, phone: str, name: str, request_type: str, request_text: str,
    related_case_id: str | None, provider_message_id: str | None,
) -> int:
    cur.execute("""
        INSERT INTO whatsapp_client_requests
            (sender_phone,sender_name,request_type,request_text,related_case_id,
             provider_message_id,status)
        VALUES (%s,%s,%s,%s,%s,%s,'NEW')
        ON CONFLICT (provider_message_id) WHERE provider_message_id IS NOT NULL
        DO UPDATE SET request_text=EXCLUDED.request_text
        RETURNING id
    """, (
        phone, name, request_type, request_text[:4000], related_case_id,
        provider_message_id,
    ))
    return int(cur.fetchone()["id"])


def _location_message() -> str:
    office = os.getenv("OFFICE_NAME", "Law Office of Ajay Chawla").strip()
    hours = os.getenv("OFFICE_HOURS", "Please confirm timings with the office.").strip()
    court = os.getenv("COURT_OFFICE_ADDRESS", "").strip()
    evening = os.getenv("EVENING_OFFICE_ADDRESS", "").strip()
    court_map = os.getenv("COURT_OFFICE_MAPS_LINK", "").strip()
    evening_map = os.getenv("EVENING_OFFICE_MAPS_LINK", "").strip()
    lines = [f"📍 {office}", "", f"🕒 {hours}"]
    if court:
        lines.extend(["", f"⚖️ Court office: {court}"])
        if court_map:
            lines.append(court_map)
    if evening:
        lines.extend(["", f"🏢 Evening office: {evening}"])
        if evening_map:
            lines.append(evening_map)
    lines.extend(["", "Please confirm before visiting on a court holiday."])
    return "\n".join(lines)


def _contact_message() -> str:
    phone = (
        os.getenv("OFFICE_PHONE_NUMBER", "").strip()
        or os.getenv("OFFICE_WHATSAPP_NUMBER", "").strip()
    )
    email = os.getenv("OFFICE_EMAIL", "").strip()
    lines = ["☎️ CONTACT THE OFFICE", ""]
    if phone:
        lines.append(f"Phone: {phone}")
    if email:
        lines.append(f"Email: {email}")
    if not phone and not email:
        lines.append("Please send your message here. The office will respond during working hours.")
    else:
        lines.extend(["", "You may also send your message here during working hours."])
    return "\n".join(lines)


def _case_summary(case: dict[str, Any]) -> str:
    return (
        "⚖️ REGISTERED CASE\n\n"
        f"Case: {case.get('case_title') or 'Case title not recorded'}\n"
        f"Number: {case.get('case_number') or 'Not recorded'}\n"
        f"Next hearing: {case.get('next_hearing') or 'Not fixed'}\n"
        f"Status: {case.get('status') or 'Not recorded'}\n\n"
        "This is an office record summary, not legal advice. Send MENU for other options."
    )


def _request_completion_reply(kind: str, request_id: int) -> str:
    labels = {
        "LEGAL_ENQUIRY": "Your legal enquiry has been sent to the office.",
        "APPOINTMENT": "Your appointment request has been sent to the office.",
        "CLIENT_IDENTIFICATION": "Your case-identification request has been sent to the office.",
        "DOCUMENTS": "Your document-checklist request has been sent to the office.",
        "GENERAL_MESSAGE": "Your message has been sent to the office.",
    }
    return (
        f"✅ {labels.get(kind, 'Your request has been sent to the office.')}\n"
        f"Reference: WR-{request_id}\n\n"
        "A member of the office will reply during working hours. Send MENU to return."
    )


def handle_client_inbound(item: dict[str, Any]) -> dict[str, Any]:
    """Handle an unlinked WhatsApp sender without exposing unrelated case data."""
    if not client_reception_enabled():
        return {"is_client": False}
    ensure_client_reception_schema()
    phone = normalize_phone(str(item.get("phone") or ""))
    name = str(item.get("name") or "WhatsApp contact").strip()[:200]
    incoming = str(item.get("text") or "").strip()
    command, argument = classify_client_command(incoming, item.get("action_id"))
    provider_id = item.get("provider_message_id")
    related_case_id = item.get("case_id")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT stage,related_case_id FROM whatsapp_client_reception_state
                WHERE sender_phone=%s AND updated_at >= NOW() - INTERVAL '2 hours'
            """, (phone,))
            state = cur.fetchone()
            if state and command == "MESSAGE":
                stage = state["stage"]
                request_type = {
                    "ENQUIRY_DETAILS": "LEGAL_ENQUIRY",
                    "APPOINTMENT_DETAILS": "APPOINTMENT",
                    "EXISTING_CLIENT_DETAILS": "CLIENT_IDENTIFICATION",
                }[stage]
                request_id = _record_request(
                    cur, phone=phone, name=name, request_type=request_type,
                    request_text=incoming, related_case_id=state.get("related_case_id"),
                    provider_message_id=provider_id,
                )
                _clear_state(cur, phone)
                conn.commit()
                return {
                    "is_client": True, "phone": phone,
                    "reply": _request_completion_reply(request_type, request_id),
                    "owner_event": request_type, "request_id": request_id,
                    "request_text": incoming, "registered": bool(related_case_id),
                }

            if command == "CANCEL":
                _clear_state(cur, phone)
                conn.commit()
                return {"is_client": True, "phone": phone, "reply": "Cancelled. Send MENU to begin again.", "owner_event": "CANCELLED"}

            if command == "MENU":
                conn.commit()
                return {
                    "is_client": True, "phone": phone, "reply": reception_menu(),
                    "list_rows": reception_menu_rows(), "list_button": "Choose option",
                    "list_section": "Client Services", "owner_event": "MENU",
                }

            if command in {"EXISTING", "CASE_STATUS"}:
                cases = _registered_cases(cur, phone)
                if cases:
                    conn.commit()
                    if len(cases) == 1:
                        return {
                            "is_client": True, "phone": phone,
                            "reply": _case_summary(cases[0]), "owner_event": "CASE_VIEW",
                            "registered": True, "case_id": cases[0].get("case_number"),
                        }
                    return {
                        "is_client": True, "phone": phone,
                        "reply": "Select one of the cases registered with this WhatsApp number.",
                        "list_rows": _case_rows(cases), "list_button": "Select case",
                        "list_section": "Registered Cases", "owner_event": "CASE_LIST",
                        "registered": True,
                    }
                _save_state(cur, phone, name, "EXISTING_CLIENT_DETAILS")
                conn.commit()
                return {
                    "is_client": True, "phone": phone,
                    "reply": (
                        "This WhatsApp number is not registered with a case. Please send "
                        "the client name and case number. The office will verify it manually; "
                        "no case information will be disclosed automatically."
                    ),
                    "owner_event": "UNMATCHED_CASE_REQUEST", "registered": False,
                }

            if command == "CASE_SELECTED":
                case = _selected_registered_case(cur, phone, argument)
                conn.commit()
                if not case:
                    return {
                        "is_client": True, "phone": phone,
                        "reply": "That case could not be verified for this number. Send MENU and try again.",
                        "owner_event": "CASE_DENIED", "registered": False,
                    }
                return {
                    "is_client": True, "phone": phone, "reply": _case_summary(case),
                    "owner_event": "CASE_VIEW", "registered": True,
                    "case_id": case.get("case_number"),
                }

            if command == "ENQUIRY":
                _save_state(cur, phone, name, "ENQUIRY_DETAILS")
                conn.commit()
                return {
                    "is_client": True, "phone": phone,
                    "reply": (
                        "Please briefly describe the legal issue, the city/court concerned, "
                        "and any urgent date. Do not send passwords, OTPs or original identity documents."
                    ),
                    "owner_event": "ENQUIRY_STARTED",
                }

            if command == "APPOINTMENT":
                _save_state(cur, phone, name, "APPOINTMENT_DETAILS")
                conn.commit()
                return {
                    "is_client": True, "phone": phone,
                    "reply": (
                        "Please send your name, purpose of meeting, preferred date/time, "
                        "and whether you prefer the court office or evening office."
                    ),
                    "owner_event": "APPOINTMENT_STARTED",
                }

            if command == "DOCUMENTS":
                request_id = _record_request(
                    cur, phone=phone, name=name, request_type="DOCUMENTS",
                    request_text="Client requested documents required checklist.",
                    related_case_id=related_case_id, provider_message_id=provider_id,
                )
                conn.commit()
                return {
                    "is_client": True, "phone": phone,
                    "reply": _request_completion_reply("DOCUMENTS", request_id),
                    "owner_event": "DOCUMENTS", "request_id": request_id,
                    "registered": bool(related_case_id),
                }

            if command == "LOCATION":
                conn.commit()
                return {"is_client": True, "phone": phone, "reply": _location_message(), "owner_event": "LOCATION"}

            if command == "CONTACT":
                conn.commit()
                return {"is_client": True, "phone": phone, "reply": _contact_message(), "owner_event": "CONTACT"}

            request_id = _record_request(
                cur, phone=phone, name=name, request_type="GENERAL_MESSAGE",
                request_text=argument or incoming, related_case_id=related_case_id,
                provider_message_id=provider_id,
            )
            conn.commit()
            return {
                "is_client": True, "phone": phone,
                "reply": _request_completion_reply("GENERAL_MESSAGE", request_id),
                "owner_event": "GENERAL_MESSAGE", "request_id": request_id,
                "request_text": argument or incoming, "registered": bool(related_case_id),
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def client_reception_status() -> dict[str, Any]:
    ensure_client_reception_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status='NEW') AS new_requests,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS requests_24h
                FROM whatsapp_client_requests
            """)
            row = dict(cur.fetchone() or {})
            row["enabled"] = client_reception_enabled()
            return row
    finally:
        conn.close()
