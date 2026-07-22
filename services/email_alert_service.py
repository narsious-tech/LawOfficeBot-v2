"""Secure IMAP monitoring for office email alerts."""
from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

import psycopg2

from config import DATABASE_URL


@dataclass(frozen=True)
class MailboxConfig:
    key: str
    label: str
    host: str
    address: str
    app_password: str


@dataclass(frozen=True)
class EmailAlert:
    mailbox_key: str
    mailbox_label: str
    uid: int
    sender: str
    subject: str
    received: str


def email_alerts_enabled() -> bool:
    return os.getenv("EMAIL_ALERTS_ENABLED", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }


def mailbox_configs() -> list[MailboxConfig]:
    values = [
        MailboxConfig(
            "gmail", "Gmail", os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com"),
            os.getenv("GMAIL_EMAIL", "").strip(),
            os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip(),
        ),
        MailboxConfig(
            "yahoo", "Yahoo Mail", os.getenv("YAHOO_IMAP_HOST", "imap.mail.yahoo.com"),
            os.getenv("YAHOO_EMAIL", "").strip(),
            os.getenv("YAHOO_APP_PASSWORD", "").replace(" ", "").strip(),
        ),
    ]
    return [item for item in values if item.address and item.app_password]


def ensure_email_alert_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS office_email_monitor_state (
                mailbox_key TEXT PRIMARY KEY,
                last_uid BIGINT NOT NULL DEFAULT 0,
                initialized BOOLEAN NOT NULL DEFAULT FALSE,
                last_checked_at TIMESTAMPTZ,
                last_success_at TIMESTAMPTZ,
                last_error TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS office_email_alert_ack (
                mailbox_key TEXT NOT NULL,
                message_uid BIGINT NOT NULL,
                telegram_user_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (mailbox_key, message_uid, telegram_user_id)
            )
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _decode(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    try:
        text = str(make_header(decode_header(value))).strip()
    except Exception:
        text = str(value).strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
    return text[:350] or fallback


def _state(mailbox_key: str) -> tuple[int, bool]:
    ensure_email_alert_schema()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT last_uid, initialized FROM office_email_monitor_state "
            "WHERE mailbox_key=%s",
            (mailbox_key,),
        )
        row = cur.fetchone()
        return (int(row[0]), bool(row[1])) if row else (0, False)
    finally:
        cur.close()
        conn.close()


def _save_state(mailbox_key: str, last_uid: int, *, error: str = "") -> None:
    ensure_email_alert_schema()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO office_email_monitor_state (
                mailbox_key, last_uid, initialized, last_checked_at,
                last_success_at, last_error, updated_at
            ) VALUES (%s,%s,TRUE,NOW(),CASE WHEN %s='' THEN NOW() ELSE NULL END,%s,NOW())
            ON CONFLICT (mailbox_key) DO UPDATE SET
                last_uid=GREATEST(office_email_monitor_state.last_uid, EXCLUDED.last_uid),
                initialized=TRUE,
                last_checked_at=NOW(),
                last_success_at=CASE WHEN EXCLUDED.last_error='' THEN NOW()
                                     ELSE office_email_monitor_state.last_success_at END,
                last_error=EXCLUDED.last_error,
                updated_at=NOW()
        """, (mailbox_key, last_uid, error, error[:1000]))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def scan_mailbox(config: MailboxConfig, maximum: int = 20) -> list[EmailAlert]:
    last_uid, initialized = _state(config.key)
    client = imaplib.IMAP4_SSL(config.host, 993, timeout=30)
    try:
        client.login(config.address, config.app_password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Inbox could not be selected.")

        status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Inbox UID search failed.")
        all_uids = [int(item) for item in (data[0] or b"").split() if item.isdigit()]
        highest = max(all_uids, default=0)

        if not initialized:
            _save_state(config.key, highest)
            return []

        new_uids = [uid for uid in all_uids if uid > last_uid][:maximum]
        alerts: list[EmailAlert] = []
        for uid in new_uids:
            status, payload = client.uid(
                "fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            if status != "OK" or not payload:
                continue
            raw = next(
                (part[1] for part in payload if isinstance(part, tuple) and part[1]),
                b"",
            )
            message = email.message_from_bytes(raw)
            display_name, sender_address = parseaddr(_decode(message.get("From"), "Unknown sender"))
            sender = display_name.strip() or sender_address.strip() or "Unknown sender"
            if sender_address and display_name:
                sender = f"{sender} <{sender_address}>"
            alerts.append(EmailAlert(
                config.key, config.label, uid, sender[:250],
                _decode(message.get("Subject"), "(No subject)"),
                _decode(message.get("Date"), "Date unavailable")[:120],
            ))

        _save_state(config.key, max(new_uids, default=last_uid))
        return alerts
    except Exception as exc:
        _save_state(config.key, last_uid, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            client.logout()
        except Exception:
            pass


def scan_all_mailboxes() -> tuple[list[EmailAlert], list[str]]:
    alerts: list[EmailAlert] = []
    errors: list[str] = []
    for config in mailbox_configs():
        try:
            alerts.extend(scan_mailbox(config))
        except Exception as exc:
            errors.append(f"{config.label}: {type(exc).__name__}: {exc}")
    return alerts, errors


def alert_recipient_ids() -> list[int]:
    names = {
        item.strip().casefold()
        for item in os.getenv("EMAIL_ALERT_STAFF_NAMES", "Preet,Priya").split(",")
        if item.strip()
    }
    if not names:
        return []

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='staff_accounts'
        """)
        columns = {str(row[0]).casefold() for row in cur.fetchall()}
        if not {"staff_name", "telegram_user_id"}.issubset(columns):
            return []
        active = " AND COALESCE(is_active, TRUE)=TRUE" if "is_active" in columns else ""
        placeholders = ",".join(["%s"] * len(names))
        cur.execute(
            "SELECT DISTINCT telegram_user_id FROM staff_accounts "
            "WHERE telegram_user_id IS NOT NULL "
            f"AND LOWER(TRIM(staff_name)) IN ({placeholders}){active}",
            tuple(sorted(names)),
        )
        return [int(row[0]) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def acknowledge(mailbox_key: str, uid: int, telegram_user_id: int, status: str) -> None:
    ensure_email_alert_schema()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO office_email_alert_ack (
                mailbox_key, message_uid, telegram_user_id, status
            ) VALUES (%s,%s,%s,%s)
            ON CONFLICT (mailbox_key, message_uid, telegram_user_id)
            DO UPDATE SET status=EXCLUDED.status, acknowledged_at=NOW()
        """, (mailbox_key, uid, telegram_user_id, status))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def monitor_status() -> list[dict]:
    ensure_email_alert_schema()
    configured = {item.key: item for item in mailbox_configs()}
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT mailbox_key,last_uid,initialized,last_checked_at,
                   last_success_at,last_error
            FROM office_email_monitor_state
            ORDER BY mailbox_key
        """)
        existing = {row[0]: row for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()

    result = []
    for key, label in (("gmail", "Gmail"), ("yahoo", "Yahoo Mail")):
        row = existing.get(key)
        result.append({
            "key": key,
            "label": label,
            "configured": key in configured,
            "last_uid": int(row[1]) if row else 0,
            "initialized": bool(row[2]) if row else False,
            "last_checked_at": row[3] if row else None,
            "last_success_at": row[4] if row else None,
            "last_error": row[5] if row else None,
        })
    return result