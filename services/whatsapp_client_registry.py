"""Owner-only registry of WhatsApp-capable clients and AD mobile conflicts."""
from __future__ import annotations

import hashlib
import re
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL


def ensure_client_registry_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS client_mobile_sync_audit (
                    id BIGSERIAL PRIMARY KEY,
                    client_id INTEGER,
                    ad_client_id TEXT,
                    client_name TEXT,
                    existing_mobile TEXT,
                    advocate_diaries_mobile TEXT,
                    action TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS client_mobile_sync_conflict_uidx
                ON client_mobile_sync_audit(
                    COALESCE(ad_client_id,''),COALESCE(existing_mobile,''),
                    COALESCE(advocate_diaries_mobile,''),action
                ) WHERE action='CONFLICT'
            """)
        conn.commit()
    finally:
        conn.close()


def normalize_registry_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    return ""


def mask_phone(value: str) -> str:
    phone = normalize_registry_phone(value)
    return f"+91 ***** {phone[-4:]}" if phone else "Invalid number"


def registry_token(phone: str) -> str:
    normalized = normalize_registry_phone(phone)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _source_priority(source: str) -> int:
    return {
        "ADVOCATE_DIARIES": 0,
        "MANUAL_CONTACT": 1,
        "CLIENT_RECORD": 2,
        "CASE_RECORD": 3,
    }.get(source, 9)


def _fetch_registry_rows() -> list[dict[str, Any]]:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    c.id AS case_db_id,
                    COALESCE(NULLIF(TRIM(c.case_number),''),NULLIF(TRIM(c.case_id),'')) AS case_number,
                    COALESCE(NULLIF(TRIM(c.case_title),''),'Case title not recorded') AS case_title,
                    COALESCE(NULLIF(TRIM(cl.client_name),''),NULLIF(TRIM(c.client_name),''),'Unknown Client') AS client_name,
                    c.mobile AS case_mobile,
                    cl.id AS client_id,
                    cl.ad_client_id,
                    cl.mobile AS client_mobile,
                    cl.whatsapp_number AS client_whatsapp,
                    cl.ad_sync_status,
                    cl.ad_synced_at,
                    cc.whatsapp_number AS contact_whatsapp,
                    cc.consent_status
                FROM cases c
                LEFT JOIN clients cl ON (
                    c.client_id=cl.id OR (
                        c.ad_client_id IS NOT NULL AND c.ad_client_id=cl.ad_client_id
                    )
                )
                LEFT JOIN client_contacts cc ON (
                    cc.is_primary=TRUE AND LOWER(TRIM(cc.case_id)) IN (
                        LOWER(TRIM(COALESCE(c.case_id,''))),
                        LOWER(TRIM(COALESCE(c.case_number,'')))
                    )
                )
                ORDER BY LOWER(COALESCE(cl.client_name,c.client_name,'')),c.id
            """)
            case_rows = [dict(row) for row in cur.fetchall()]
            cur.execute("""
                SELECT id AS client_id,ad_client_id,client_name,mobile AS client_mobile,
                       whatsapp_number AS client_whatsapp,ad_sync_status,ad_synced_at
                FROM clients
                WHERE NULLIF(TRIM(COALESCE(whatsapp_number,mobile,'')),'') IS NOT NULL
                ORDER BY LOWER(client_name),id
            """)
            client_rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    grouped: dict[str, dict[str, Any]] = {}

    def add_phone(row: dict[str, Any], raw_phone: Any, source: str) -> None:
        phone = normalize_registry_phone(raw_phone)
        if not phone:
            return
        entry = grouped.setdefault(phone, {
            "phone": phone,
            "token": registry_token(phone),
            "client_name": row.get("client_name") or "Unknown Client",
            "client_id": row.get("client_id"),
            "ad_client_id": row.get("ad_client_id"),
            "ad_sync_status": row.get("ad_sync_status"),
            "ad_synced_at": row.get("ad_synced_at"),
            "consent_status": row.get("consent_status"),
            "sources": set(),
            "cases": {},
        })
        entry["sources"].add(source)
        if _source_priority(source) < _source_priority(entry.get("primary_source", "")):
            entry["primary_source"] = source
        if not entry.get("ad_client_id") and row.get("ad_client_id"):
            entry["ad_client_id"] = row.get("ad_client_id")
            entry["client_id"] = row.get("client_id")
            entry["ad_sync_status"] = row.get("ad_sync_status")
            entry["ad_synced_at"] = row.get("ad_synced_at")
        if row.get("case_db_id"):
            entry["cases"][int(row["case_db_id"])] = {
                "case_number": row.get("case_number") or "Case number not recorded",
                "case_title": row.get("case_title") or "Case title not recorded",
            }

    for row in case_rows:
        add_phone(row, row.get("client_whatsapp"), "ADVOCATE_DIARIES" if row.get("ad_client_id") else "CLIENT_RECORD")
        add_phone(row, row.get("client_mobile"), "ADVOCATE_DIARIES" if row.get("ad_client_id") else "CLIENT_RECORD")
        add_phone(row, row.get("contact_whatsapp"), "MANUAL_CONTACT")
        add_phone(row, row.get("case_mobile"), "CASE_RECORD")
    for row in client_rows:
        add_phone(row, row.get("client_whatsapp"), "ADVOCATE_DIARIES" if row.get("ad_client_id") else "CLIENT_RECORD")
        add_phone(row, row.get("client_mobile"), "ADVOCATE_DIARIES" if row.get("ad_client_id") else "CLIENT_RECORD")

    result = []
    for entry in grouped.values():
        entry["sources"] = sorted(entry["sources"], key=_source_priority)
        entry["cases"] = list(entry["cases"].values())
        entry["case_count"] = len(entry["cases"])
        result.append(entry)
    return sorted(result, key=lambda row: (str(row["client_name"]).casefold(), row["phone"]))


def list_registered_clients(
    *, search: str = "", page: int = 1, page_size: int = 8,
) -> dict[str, Any]:
    rows = _fetch_registry_rows()
    needle = str(search or "").strip().casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join([
            str(row.get("client_name") or ""),
            str(row.get("phone") or ""),
            str(row.get("ad_client_id") or ""),
            " ".join(
                f"{case.get('case_number','')} {case.get('case_title','')}"
                for case in row.get("cases") or []
            ),
        ]).casefold()]
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), pages))
    start = (page - 1) * page_size
    return {
        "rows": rows[start:start + page_size],
        "total": total,
        "page": page,
        "pages": pages,
        "search": search,
    }


def registered_client_detail(token: str) -> dict[str, Any] | None:
    for row in _fetch_registry_rows():
        if row["token"] == token:
            return row
    return None


def mobile_sync_conflicts(limit: int = 20) -> list[dict[str, Any]]:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id,client_name,ad_client_id,existing_mobile,
                       advocate_diaries_mobile,created_at
                FROM client_mobile_sync_audit
                WHERE action='CONFLICT'
                ORDER BY created_at DESC,id DESC LIMIT %s
            """, (max(1, min(int(limit), 100)),))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
