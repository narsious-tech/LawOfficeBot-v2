"""Consent-aware, cost-capped WhatsApp hearing and case update notices."""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.whatsapp_case_notification_rules import (
    is_closed_status,
    is_material_purpose,
    parse_case_date,
)
from services.whatsapp_cloud import normalize_phone, send_template_message, transport_ready


IST = ZoneInfo("Asia/Kolkata")
TRUE_VALUES = {"1", "true", "yes", "on"}
CONSENTED = {"OPTED_IN", "CONSENTED", "YES", "ACTIVE", "VERIFIED"}


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_VALUES


def notification_config() -> dict[str, Any]:
    return {
        "reminders_enabled": _enabled("WHATSAPP_CASE_REMINDERS_ENABLED"),
        "updates_enabled": _enabled("WHATSAPP_CASE_STATUS_UPDATES_ENABLED"),
        "poll_enabled": _enabled("ADVOCATE_DIARIES_STATUS_POLL_ENABLED"),
        "reminder_days": max(1, int(os.getenv("WHATSAPP_CASE_REMINDER_DAYS", "2"))),
        "reminder_template": os.getenv(
            "WHATSAPP_CASE_REMINDER_TEMPLATE", "case_hearing_reminder"
        ).strip(),
        "update_template": os.getenv(
            "WHATSAPP_CASE_STATUS_TEMPLATE", "case_status_update"
        ).strip(),
        "language": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en").strip() or "en",
        "monthly_cap": Decimal(
            os.getenv("WHATSAPP_CASE_NOTIFICATIONS_MONTHLY_CAP_INR", "150")
        ),
        "estimated_rate": Decimal(os.getenv("WHATSAPP_UTILITY_RATE_INR", "0.115")),
    }


def ensure_case_notification_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_client_consent (
                    phone_number TEXT PRIMARY KEY,
                    consent_status TEXT NOT NULL DEFAULT 'OPTED_OUT',
                    consent_source TEXT NOT NULL,
                    policy_version TEXT NOT NULL DEFAULT '2026-10',
                    consented_at TIMESTAMPTZ,
                    opted_out_at TIMESTAMPTZ,
                    updated_by BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_case_notification_state (
                    case_db_id INTEGER PRIMARY KEY,
                    case_ref TEXT,
                    case_title TEXT,
                    client_name TEXT,
                    phone_number TEXT,
                    hearing_date DATE,
                    case_status TEXT,
                    purpose TEXT,
                    snapshot_hash TEXT NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_case_notification_ledger (
                    id BIGSERIAL PRIMARY KEY,
                    event_key TEXT UNIQUE NOT NULL,
                    case_db_id INTEGER,
                    case_ref TEXT,
                    phone_number TEXT,
                    event_type TEXT NOT NULL,
                    hearing_date DATE,
                    template_name TEXT,
                    message_body TEXT,
                    delivery_status TEXT NOT NULL DEFAULT 'PENDING',
                    provider_message_id TEXT,
                    provider_error TEXT,
                    estimated_cost_inr NUMERIC(10,4) NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS whatsapp_case_notice_month_idx
                ON whatsapp_case_notification_ledger(created_at,delivery_status)
            """)
        conn.commit()
    finally:
        conn.close()


def set_phone_consent(
    phone: str, opted_in: bool, *, source: str, updated_by: int | None = None
) -> dict[str, Any]:
    ensure_case_notification_schema()
    normalized = normalize_phone(phone)
    status = "OPTED_IN" if opted_in else "OPTED_OUT"
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO whatsapp_client_consent(
                    phone_number,consent_status,consent_source,consented_at,opted_out_at,
                    updated_by,updated_at
                ) VALUES (
                    %s,%s,%s,CASE WHEN %s THEN NOW() END,
                    CASE WHEN NOT %s THEN NOW() END,%s,NOW()
                )
                ON CONFLICT(phone_number) DO UPDATE SET
                    consent_status=EXCLUDED.consent_status,
                    consent_source=EXCLUDED.consent_source,
                    consented_at=CASE WHEN %s THEN NOW() ELSE whatsapp_client_consent.consented_at END,
                    opted_out_at=CASE WHEN NOT %s THEN NOW() ELSE NULL END,
                    updated_by=EXCLUDED.updated_by,updated_at=NOW()
                RETURNING phone_number,consent_status,consent_source,updated_at
            """, (
                normalized, status, source[:60], opted_in, opted_in, updated_by,
                opted_in, opted_in,
            ))
            row = dict(cur.fetchone())
        conn.commit()
        return row
    finally:
        conn.close()


def consent_status_for_phone(phone: str) -> str:
    ensure_case_notification_schema()
    normalized = normalize_phone(phone)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT consent_status FROM whatsapp_client_consent WHERE phone_number=%s",
                (normalized,),
            )
            row = cur.fetchone()
            return str(row[0]) if row else "NOT_REQUESTED"
    finally:
        conn.close()


def _case_rows(cur) -> list[dict[str, Any]]:
    cur.execute("""
        SELECT DISTINCT ON (c.id)
            c.id AS case_db_id,
            COALESCE(NULLIF(TRIM(c.case_number),''),NULLIF(TRIM(c.case_id),'')) AS case_ref,
            COALESCE(NULLIF(TRIM(c.case_title),''),'Case title not recorded') AS case_title,
            COALESCE(NULLIF(TRIM(cl.client_name),''),NULLIF(TRIM(c.client_name),''),'Client') AS client_name,
            COALESCE(c.next_hearing,c.hearing_date) AS next_hearing,
            COALESCE(NULLIF(TRIM(c.status),''),'pending') AS case_status,
            COALESCE(NULLIF(TRIM(c.notes),''),'') AS purpose,
            COALESCE(
                NULLIF(REGEXP_REPLACE(COALESCE(cc.whatsapp_number,''),'[^0-9]','','g'),''),
                NULLIF(REGEXP_REPLACE(COALESCE(cl.whatsapp_number,''),'[^0-9]','','g'),''),
                NULLIF(REGEXP_REPLACE(COALESCE(cl.mobile,''),'[^0-9]','','g'),''),
                NULLIF(REGEXP_REPLACE(COALESCE(c.mobile,''),'[^0-9]','','g'),'')
            ) AS phone_number,
            UPPER(COALESCE(wcc.consent_status,cc.consent_status,'NOT_REQUESTED')) AS consent_status
        FROM cases c
        LEFT JOIN clients cl ON c.client_id=cl.id OR (
            c.ad_client_id IS NOT NULL AND c.ad_client_id=cl.ad_client_id
        )
        LEFT JOIN client_contacts cc ON cc.is_primary=TRUE AND LOWER(TRIM(cc.case_id)) IN (
            LOWER(TRIM(COALESCE(c.case_id,''))),LOWER(TRIM(COALESCE(c.case_number,'')))
        )
        LEFT JOIN whatsapp_client_consent wcc ON wcc.phone_number=COALESCE(
            NULLIF(REGEXP_REPLACE(COALESCE(cc.whatsapp_number,''),'[^0-9]','','g'),''),
            NULLIF(REGEXP_REPLACE(COALESCE(cl.whatsapp_number,''),'[^0-9]','','g'),''),
            NULLIF(REGEXP_REPLACE(COALESCE(cl.mobile,''),'[^0-9]','','g'),''),
            NULLIF(REGEXP_REPLACE(COALESCE(c.mobile,''),'[^0-9]','','g'),'')
        )
        ORDER BY c.id,cc.id DESC NULLS LAST
    """)
    rows = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT phone_number,consent_status FROM whatsapp_client_consent")
    consent_by_phone = {
        str(row['phone_number']): str(row['consent_status']).upper()
        for row in cur.fetchall()
    }
    for row in rows:
        try:
            row["phone_number"] = normalize_phone(str(row.get("phone_number") or ""))
        except ValueError:
            row["phone_number"] = ""
        if row["phone_number"] in consent_by_phone:
            row["consent_status"] = consent_by_phone[row["phone_number"]]
    return rows


def _snapshot_hash(row: dict[str, Any], hearing: date | None) -> str:
    value = "|".join([
        str(hearing or ""), str(row.get("case_status") or "").strip().casefold(),
        str(row.get("purpose") or "").strip().casefold(), str(row.get("phone_number") or ""),
    ])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_key(kind: str, row: dict[str, Any], detail: str) -> str:
    raw = f"{kind}|{row['case_db_id']}|{row.get('phone_number')}|{detail}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _month_spend(cur) -> Decimal:
    cur.execute("""
        SELECT COALESCE(SUM(estimated_cost_inr),0) AS month_spend
        FROM whatsapp_case_notification_ledger
        WHERE delivery_status IN ('PENDING','SENT_API','SENT','DELIVERED','READ')
          AND (created_at AT TIME ZONE 'Asia/Kolkata') >=
              DATE_TRUNC('month',NOW() AT TIME ZONE 'Asia/Kolkata')
    """)
    return Decimal(str(cur.fetchone()['month_spend'] or 0))


def _claim_event(
    cur, *, event_key: str, row: dict[str, Any], event_type: str,
    hearing: date | None, template: str, body: str, rate: Decimal,
) -> int | None:
    cur.execute("""
        INSERT INTO whatsapp_case_notification_ledger(
            event_key,case_db_id,case_ref,phone_number,event_type,hearing_date,
            template_name,message_body,estimated_cost_inr,attempts
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
        ON CONFLICT(event_key) DO UPDATE SET
            delivery_status='PENDING',provider_error=NULL,attempts=
                whatsapp_case_notification_ledger.attempts+1,updated_at=NOW()
        WHERE whatsapp_case_notification_ledger.delivery_status='FAILED'
          AND whatsapp_case_notification_ledger.attempts < 3
          AND whatsapp_case_notification_ledger.updated_at < NOW()-INTERVAL '30 minutes'
        RETURNING id
    """, (
        event_key, row["case_db_id"], row.get("case_ref"), row.get("phone_number"),
        event_type, hearing, template, body[:1024], rate,
    ))
    result = cur.fetchone()
    return int(result['id']) if result else None


def _send_event(
    conn, cur, *, row: dict[str, Any], kind: str, detail: str,
    hearing: date | None, template: str, body: str, cfg: dict[str, Any],
) -> str:
    cur.execute("SELECT pg_advisory_xact_lock(4320261002)")
    if not row.get("phone_number") or str(row.get("consent_status") or "").upper() not in CONSENTED:
        return "INELIGIBLE"
    if _month_spend(cur) + cfg["estimated_rate"] > cfg["monthly_cap"]:
        return "CAP_REACHED"
    key = _event_key(kind, row, detail)
    ledger_id = _claim_event(
        cur, event_key=key, row=row, event_type=kind, hearing=hearing,
        template=template, body=body, rate=cfg["estimated_rate"],
    )
    if not ledger_id:
        return "DUPLICATE"
    conn.commit()
    try:
        result = send_template_message(
            row["phone_number"], template, body, cfg["language"]
        )
    except Exception as exc:
        cur.execute("""
            UPDATE whatsapp_case_notification_ledger
            SET delivery_status='FAILED',provider_error=%s,updated_at=NOW() WHERE id=%s
        """, (str(exc)[:1000], ledger_id))
        conn.commit()
        return "FAILED"
    cur.execute("""
        UPDATE whatsapp_case_notification_ledger
        SET delivery_status='SENT_API',provider_message_id=%s,sent_at=NOW(),updated_at=NOW()
        WHERE id=%s
    """, (result["provider_message_id"], ledger_id))
    conn.commit()
    return "SENT"


def scan_case_notifications(*, dry_run: bool = False, today: date | None = None) -> dict[str, Any]:
    """Compare case snapshots and send only eligible D-2/material-change notices."""
    ensure_case_notification_schema()
    cfg = notification_config()
    today = today or datetime.now(IST).date()
    stats: dict[str, Any] = {
        "cases": 0, "due_d2": 0, "material_changes": 0, "sent": 0,
        "duplicates": 0, "ineligible": 0, "failed": 0, "cap_reached": False,
        "dry_run": dry_run, "reminders_enabled": cfg["reminders_enabled"],
        "updates_enabled": cfg["updates_enabled"], "preview": [],
    }
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE whatsapp_case_notification_ledger
                SET delivery_status='FAILED',
                    provider_error=COALESCE(provider_error,'Interrupted before Meta submission')
                WHERE delivery_status='PENDING'
                  AND updated_at < NOW()-INTERVAL '15 minutes'
            """)
            rows = _case_rows(cur)
            for row in rows:
                stats["cases"] += 1
                hearing = parse_case_date(row.get("next_hearing"))
                digest = _snapshot_hash(row, hearing)
                cur.execute(
                    "SELECT * FROM whatsapp_case_notification_state WHERE case_db_id=%s",
                    (row["case_db_id"],),
                )
                previous = cur.fetchone()
                events: list[tuple[str, str, str, str]] = []
                if previous:
                    old_date = previous.get("hearing_date")
                    if old_date != hearing and hearing:
                        body = (
                            f"{row['case_title']} ({row.get('case_ref') or 'case number not recorded'}). "
                            f"Hearing date changed from {old_date.strftime('%d-%m-%Y') if old_date else 'not fixed'} "
                            f"to {hearing.strftime('%d-%m-%Y')}."
                        )
                        events.append(("DATE_CHANGED", str(hearing), cfg["update_template"], body))
                    old_status = str(previous.get("case_status") or "")
                    new_status = str(row.get("case_status") or "")
                    if old_status.casefold() != new_status.casefold() and (
                        is_closed_status(old_status) or is_closed_status(new_status)
                    ):
                        body = (
                            f"{row['case_title']} ({row.get('case_ref') or 'case number not recorded'}). "
                            f"Status changed from {old_status or 'not recorded'} to {new_status or 'not recorded'}."
                        )
                        events.append(("STATUS_CHANGED", new_status, cfg["update_template"], body))
                    old_purpose = str(previous.get("purpose") or "")
                    new_purpose = str(row.get("purpose") or "")
                    if old_purpose.casefold() != new_purpose.casefold() and is_material_purpose(new_purpose):
                        body = (
                            f"{row['case_title']} ({row.get('case_ref') or 'case number not recorded'}). "
                            f"Action required: {new_purpose}."
                        )
                        events.append(("ACTION_REQUIRED", new_purpose, cfg["update_template"], body))

                date_changed = any(event[0] == "DATE_CHANGED" for event in events)
                if len(events) > 1:
                    combined_detail = "|".join(
                        f"{kind}:{detail}" for kind, detail, _template, _body in events
                    )
                    combined_body = "\n".join(body for _kind, _detail, _template, body in events)
                    events = [(
                        "MATERIAL_UPDATE", combined_detail, cfg["update_template"], combined_body,
                    )]
                if (
                    hearing and hearing == today + timedelta(days=cfg["reminder_days"])
                    and not is_closed_status(row.get("case_status"))
                ):
                    stats["due_d2"] += 1
                    if not date_changed:
                        body = (
                            f"{row['case_title']} ({row.get('case_ref') or 'case number not recorded'}) "
                            f"is listed on {hearing.strftime('%d-%m-%Y')}. "
                            f"Current office status: {row.get('case_status') or 'pending'}."
                        )
                        events.append(("D2_REMINDER", str(hearing), cfg["reminder_template"], body))
                stats["material_changes"] += sum(1 for event in events if event[0] != "D2_REMINDER")

                for kind, detail, template, body in events:
                    enabled = cfg["reminders_enabled"] if kind == "D2_REMINDER" else cfg["updates_enabled"]
                    if not enabled or dry_run or not transport_ready():
                        if len(stats["preview"]) < 15:
                            stats["preview"].append({
                                "event": kind, "case": row.get("case_ref"),
                                "phone": str(row.get("phone_number") or "")[-4:] or "none",
                                "consent": row.get("consent_status"), "enabled": enabled,
                            })
                        continue
                    outcome = _send_event(
                        conn, cur, row=row, kind=kind, detail=detail, hearing=hearing,
                        template=template, body=body, cfg=cfg,
                    )
                    if outcome == "SENT":
                        stats["sent"] += 1
                    elif outcome == "DUPLICATE":
                        stats["duplicates"] += 1
                    elif outcome == "INELIGIBLE":
                        stats["ineligible"] += 1
                    elif outcome == "CAP_REACHED":
                        stats["cap_reached"] = True
                    elif outcome == "FAILED":
                        stats["failed"] += 1

                cur.execute("""
                    INSERT INTO whatsapp_case_notification_state(
                        case_db_id,case_ref,case_title,client_name,phone_number,hearing_date,
                        case_status,purpose,snapshot_hash,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT(case_db_id) DO UPDATE SET
                        case_ref=EXCLUDED.case_ref,case_title=EXCLUDED.case_title,
                        client_name=EXCLUDED.client_name,phone_number=EXCLUDED.phone_number,
                        hearing_date=EXCLUDED.hearing_date,case_status=EXCLUDED.case_status,
                        purpose=EXCLUDED.purpose,snapshot_hash=EXCLUDED.snapshot_hash,updated_at=NOW()
                """, (
                    row["case_db_id"], row.get("case_ref"), row.get("case_title"),
                    row.get("client_name"), row.get("phone_number"), hearing,
                    row.get("case_status"), row.get("purpose"), digest,
                ))
            stats["month_spend"] = float(_month_spend(cur))
            stats["monthly_cap"] = float(cfg["monthly_cap"])
        conn.commit()
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def case_notification_status() -> dict[str, Any]:
    ensure_case_notification_schema()
    cfg = notification_config()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE consent_status='OPTED_IN') AS opted_in,
                    COUNT(*) FILTER (WHERE consent_status='OPTED_OUT') AS opted_out
                FROM whatsapp_client_consent
            """)
            consent = dict(cur.fetchone() or {})
            cur.execute("""
                SELECT COUNT(*) AS sent_month
                FROM whatsapp_case_notification_ledger
                WHERE delivery_status IN ('PENDING','SENT_API','SENT','DELIVERED','READ')
                  AND (created_at AT TIME ZONE 'Asia/Kolkata') >=
                      DATE_TRUNC('month',NOW() AT TIME ZONE 'Asia/Kolkata')
            """)
            sent = dict(cur.fetchone() or {})
            return {
                **cfg, **consent, **sent, "month_spend": float(_month_spend(cur)),
                "transport_ready": transport_ready(),
            }
    finally:
        conn.close()
