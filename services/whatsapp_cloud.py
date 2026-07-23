"""Meta WhatsApp Cloud API transport, webhook persistence and status tracking."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import psycopg2
import requests
from psycopg2.extras import RealDictCursor, Json

from config import DATABASE_URL


def whatsapp_config() -> dict[str, Any]:
    return {
        "enabled": os.getenv("WHATSAPP_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        "phone_number_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        "business_account_id": os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip(),
        "access_token": os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip(),
        "verify_token": os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip(),
        "app_secret": os.getenv("WHATSAPP_APP_SECRET", "").strip(),
        "graph_version": os.getenv("WHATSAPP_GRAPH_VERSION", "v23.0").strip(),
    }


def transport_ready() -> bool:
    cfg = whatsapp_config()
    return bool(
        cfg["enabled"] and cfg["phone_number_id"] and cfg["access_token"]
        and cfg["verify_token"]
    )


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) < 11 or len(digits) > 15:
        raise ValueError("Use a valid WhatsApp number in international format.")
    return digits


def ensure_whatsapp_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS provider_message_id TEXT
            """)
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS provider_error TEXT
            """)
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ
            """)
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ
            """)
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0
            """)
            cur.execute("""
                ALTER TABLE client_messages
                ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                client_messages_provider_message_uidx
                ON client_messages(provider_message_id)
                WHERE provider_message_id IS NOT NULL
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_inbound_messages (
                    id BIGSERIAL PRIMARY KEY,
                    provider_message_id TEXT UNIQUE NOT NULL,
                    sender_phone TEXT NOT NULL,
                    sender_name TEXT,
                    message_type TEXT NOT NULL,
                    message_text TEXT,
                    related_case_id TEXT,
                    raw_payload JSONB NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    acknowledged_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
                    id BIGSERIAL PRIMARY KEY,
                    event_key TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    raw_payload JSONB NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS whatsapp_inbound_phone_idx
                ON whatsapp_inbound_messages(sender_phone,received_at DESC)
            """)
        conn.commit()
    finally:
        conn.close()


def _graph_url() -> str:
    cfg = whatsapp_config()
    return (
        f"https://graph.facebook.com/{cfg['graph_version']}/"
        f"{cfg['phone_number_id']}/messages"
    )


def send_text_message(phone: str, text: str) -> dict[str, Any]:
    cfg = whatsapp_config()
    if not transport_ready():
        raise RuntimeError(
            "WhatsApp Cloud API is not enabled or its Railway variables are incomplete."
        )
    response = requests.post(
        _graph_url(),
        headers={
            "Authorization": f"Bearer {cfg['access_token']}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalize_phone(phone),
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        },
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:1000]}
    if response.status_code >= 400:
        message = (
            ((payload.get("error") or {}).get("message"))
            or f"HTTP {response.status_code}"
        )
        raise RuntimeError(f"Meta WhatsApp rejected the message: {message}")
    provider_id = ((payload.get("messages") or [{}])[0]).get("id")
    if not provider_id:
        raise RuntimeError("Meta accepted the request without returning a message ID.")
    return {"provider_message_id": provider_id, "response": payload}


def send_template_message(
    phone: str, template_name: str, body_value: str, language: str = "en"
) -> dict[str, Any]:
    cfg = whatsapp_config()
    if not transport_ready():
        raise RuntimeError("WhatsApp Cloud API configuration is incomplete.")
    response = requests.post(
        _graph_url(),
        headers={
            "Authorization": f"Bearer {cfg['access_token']}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": normalize_phone(phone),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": body_value[:1024]}],
                }],
            },
        },
        timeout=30,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        message = ((payload.get("error") or {}).get("message")) or f"HTTP {response.status_code}"
        raise RuntimeError(f"Meta rejected template '{template_name}': {message}")
    provider_id = ((payload.get("messages") or [{}])[0]).get("id")
    if not provider_id:
        raise RuntimeError("Meta accepted the template without returning a message ID.")
    return {"provider_message_id": provider_id, "response": payload}


def _freeform_window_open(cur, phone: str) -> bool:
    cur.execute("""
        SELECT 1 FROM whatsapp_inbound_messages
        WHERE sender_phone=%s AND received_at >= NOW() - INTERVAL '24 hours'
        LIMIT 1
    """, (normalize_phone(phone),))
    return bool(cur.fetchone())


def send_logged_client_message(message_id: int) -> dict[str, Any]:
    ensure_whatsapp_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id,phone_number,message_text,message_type,delivery_status
                FROM client_messages WHERE id=%s FOR UPDATE
            """, (message_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Client message was not found.")
            if row["delivery_status"] not in {"DRAFT", "FAILED", "RETRY_PENDING"}:
                raise ValueError(
                    f"Message is already in status {row['delivery_status']}."
                )
            try:
                if _freeform_window_open(cur, row["phone_number"]):
                    result = send_text_message(row["phone_number"], row["message_text"])
                else:
                    template_name = (
                        os.getenv(
                            f"WHATSAPP_TEMPLATE_{str(row['message_type']).upper()}", ""
                        ).strip()
                        or os.getenv("WHATSAPP_DEFAULT_TEMPLATE", "").strip()
                    )
                    if not template_name:
                        raise RuntimeError(
                            "The client has not messaged within 24 hours. Configure an "
                            "approved Meta template (WHATSAPP_TEMPLATE_"
                            f"{str(row['message_type']).upper()}) or use Open WhatsApp."
                        )
                    result = send_template_message(
                        row["phone_number"],
                        template_name,
                        row["message_text"],
                        os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en").strip() or "en",
                    )
            except Exception as exc:
                cur.execute("""
                    UPDATE client_messages
                    SET delivery_status='RETRY_PENDING',provider_error=%s,
                        retry_count=COALESCE(retry_count,0)+1,
                        next_retry_at=CASE
                            WHEN COALESCE(retry_count,0)+1 < 5
                            THEN NOW() + INTERVAL '10 minutes'
                            ELSE NULL
                        END
                    WHERE id=%s
                """, (str(exc)[:1000], message_id))
                conn.commit()
                raise
            cur.execute("""
                UPDATE client_messages
                SET delivery_status='SENT_API',sent_at=NOW(),
                    provider_message_id=%s,provider_error=NULL,next_retry_at=NULL
                WHERE id=%s
            """, (result["provider_message_id"], message_id))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retry_due_messages(limit: int = 20) -> list[dict[str, Any]]:
    ensure_whatsapp_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id FROM client_messages
                WHERE delivery_status='RETRY_PENDING'
                  AND next_retry_at IS NOT NULL AND next_retry_at <= NOW()
                  AND COALESCE(retry_count,0) < 5
                ORDER BY next_retry_at,id LIMIT %s
            """, (max(1, min(limit, 50)),))
            ids = [int(row["id"]) for row in cur.fetchall()]
    finally:
        conn.close()
    results = []
    for message_id in ids:
        try:
            sent = send_logged_client_message(message_id)
            results.append({"id": message_id, "sent": True, **sent})
        except Exception as exc:
            results.append({"id": message_id, "sent": False, "error": str(exc)})
    return results


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = whatsapp_config()["app_secret"]
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def verify_challenge(mode: str, token: str, challenge: str) -> str | None:
    cfg = whatsapp_config()
    if mode == "subscribe" and cfg["verify_token"] and hmac.compare_digest(
        token or "", cfg["verify_token"]
    ):
        return challenge
    return None


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _find_case_for_phone(cur, phone: str) -> str | None:
    cur.execute("""
        SELECT case_ref FROM (
            SELECT
                COALESCE(NULLIF(TRIM(c.case_number),''),NULLIF(TRIM(c.case_id),'')) AS case_ref,
                c.id AS sort_id
            FROM cases c
            WHERE REGEXP_REPLACE(COALESCE(c.mobile,''),'[^0-9]','','g') IN (%s,%s)
            UNION ALL
            SELECT cc.case_id AS case_ref, cc.id AS sort_id
            FROM client_contacts cc
            WHERE REGEXP_REPLACE(COALESCE(cc.whatsapp_number,''),'[^0-9]','','g') IN (%s,%s)
        ) matched
        WHERE case_ref IS NOT NULL
        ORDER BY sort_id DESC LIMIT 1
    """, (phone, phone[-10:], phone, phone[-10:]))
    row = cur.fetchone()
    return row[0] if row else None


def process_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist incoming messages/statuses. Returns new inbound alerts."""
    ensure_whatsapp_schema()
    inbound_alerts: list[dict[str, Any]] = []
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            for entry in payload.get("entry") or []:
                for change in entry.get("changes") or []:
                    value = change.get("value") or {}
                    contacts = {
                        item.get("wa_id"): (item.get("profile") or {}).get("name")
                        for item in value.get("contacts") or []
                    }
                    for status in value.get("statuses") or []:
                        provider_id = status.get("id")
                        state = (status.get("status") or "").upper()
                        if not provider_id or not state:
                            continue
                        event_key = f"status:{provider_id}:{state}:{status.get('timestamp','')}"
                        cur.execute("""
                            INSERT INTO whatsapp_webhook_events(event_key,event_type,raw_payload)
                            VALUES (%s,'STATUS',%s) ON CONFLICT DO NOTHING
                        """, (event_key, Json(status)))
                        if cur.rowcount == 0:
                            continue
                        assignments = ["delivery_status=%s"]
                        params: list[Any] = [state]
                        stamp = _timestamp(status.get("timestamp"))
                        if state == "DELIVERED":
                            assignments.append("delivered_at=%s")
                            params.append(stamp)
                        elif state == "READ":
                            assignments.append("read_at=%s")
                            params.append(stamp)
                        elif state == "FAILED":
                            assignments.append("provider_error=%s")
                            params.append(json.dumps(status.get("errors") or [])[:1000])
                        params.append(provider_id)
                        cur.execute(
                            "UPDATE client_messages SET " + ",".join(assignments)
                            + " WHERE provider_message_id=%s",
                            tuple(params),
                        )
                    for message in value.get("messages") or []:
                        provider_id = message.get("id")
                        phone = normalize_phone(message.get("from") or "")
                        kind = message.get("type") or "unknown"
                        text = (
                            ((message.get("text") or {}).get("body"))
                            or ((message.get("button") or {}).get("text"))
                            or ((message.get("interactive") or {}).get("button_reply") or {}).get("title")
                            or f"[{kind} message]"
                        )
                        case_id = _find_case_for_phone(cur, phone)
                        cur.execute("""
                            INSERT INTO whatsapp_inbound_messages(
                                provider_message_id,sender_phone,sender_name,message_type,
                                message_text,related_case_id,raw_payload,received_at
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,NOW()))
                            ON CONFLICT(provider_message_id) DO NOTHING
                        """, (
                            provider_id, phone, contacts.get(phone), kind, text,
                            case_id, Json(message), _timestamp(message.get("timestamp")),
                        ))
                        if cur.rowcount:
                            inbound_alerts.append({
                                "provider_message_id": provider_id,
                                "phone": phone,
                                "name": contacts.get(phone),
                                "text": text,
                                "case_id": case_id,
                                "type": kind,
                            })
        conn.commit()
        return inbound_alerts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recent_inbound(limit: int = 20) -> list[dict[str, Any]]:
    ensure_whatsapp_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM whatsapp_inbound_messages
                ORDER BY received_at DESC,id DESC LIMIT %s
            """, (max(1, min(limit, 50)),))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
