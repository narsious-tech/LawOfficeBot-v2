"""Download unseen eCourts order PDFs through the eCourtsIndia partner API.

The API is only an upstream transport.  Downloaded PDFs are placed in the
existing Google Drive eCourts Order Inbox, where the established matching,
archiving, AI-note and alert workflow remains authoritative.
"""
from __future__ import annotations

import base64
import io
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import psycopg2
import requests
from googleapiclient.http import MediaIoBaseUpload

from config import DATABASE_URL
from utils.drive import get_drive_service


BASE_URL = "https://webapi.ecourtsindia.com"
IST = ZoneInfo("Asia/Kolkata")


def _conn():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="law-office-ecourtsindia-api",
    )


def _api_key() -> str:
    return os.getenv("ECOURTSINDIA_API_KEY", "").strip()


def api_enabled() -> bool:
    return bool(_api_key()) and os.getenv(
        "ECOURTSINDIA_API_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}


def ensure_api_schema() -> None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_api_order_downloads (
                id BIGSERIAL PRIMARY KEY,
                cino TEXT NOT NULL,
                order_filename TEXT NOT NULL,
                order_date DATE,
                order_kind TEXT NOT NULL,
                signed_copy BOOLEAN NOT NULL DEFAULT TRUE,
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                drive_file_id TEXT,
                drive_file_link TEXT,
                api_request_id TEXT,
                error_message TEXT,
                discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                downloaded_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(cino, order_filename, signed_copy)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_api_order_status
            ON ecourts_api_order_downloads(status, updated_at)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_api_case_checks (
                cino TEXT PRIMARY KEY,
                checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                order_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _request(method: str, path: str, *, timeout: int = 90) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("ECOURTSINDIA_API_KEY is not configured in Railway.")
    response = requests.request(
        method,
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"eCourtsIndia returned non-JSON content (HTTP {response.status_code})."
        ) from exc
    if response.status_code >= 400:
        detail = payload.get("message") or payload.get("error") or str(payload)
        raise RuntimeError(
            f"eCourtsIndia HTTP {response.status_code}: {str(detail)[:500]}"
        )
    return payload


def _compact_case_number(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _current_advocate_diaries_cases() -> tuple[list[str], int, str, str]:
    """Return exact case-number keys from today's live Advocate Diaries list.

    This intentionally reads the current source instead of the persistent live
    hearing mirror, which can contain rows imported earlier in the day.
    """
    from commands.dashboard import fetch_advocate_diaries_cause_groups

    today = datetime.now(IST).date()
    groups, source = fetch_advocate_diaries_cause_groups(today)
    numbers = {
        _compact_case_number(case.get("case_number"))
        for group in groups or []
        for case in group.get("cases", []) or []
        if _compact_case_number(case.get("case_number"))
    }
    return sorted(numbers), len(numbers), str(source), today.isoformat()


def _approved_cases(
    limit: int, current_case_numbers: list[str], force: bool = False
) -> list[tuple[str, str]]:
    if not current_case_numbers:
        return []
    conn = _conn()
    cur = conn.cursor()
    try:
        refresh_hours = max(1, int(os.getenv("ECOURTSINDIA_CASE_CHECK_HOURS", "24")))
        cur.execute("""
            SELECT DISTINCT l.cino,
                   COALESCE(l.local_case_number, c.case_number, c.case_id,
                            b.display_case_number, l.cino)
            FROM ecourts_case_links l
            LEFT JOIN ecourts_backup_records b ON b.cino=l.cino
            LEFT JOIN cases c ON c.id::text=l.local_case_pk
            LEFT JOIN ecourts_api_case_checks ck ON ck.cino=l.cino
            WHERE l.link_status='APPROVED'
              AND l.cino ~ '^[A-Z]{4}[0-9]{12}$'
              AND COALESCE(UPPER(TRIM(c.status)), 'OPEN') NOT IN ('CLOSED','DISPOSED')
              AND (
                    LOWER(REGEXP_REPLACE(COALESCE(l.local_case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(c.case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(c.case_id,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(b.display_case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
              )
              AND (%s OR ck.checked_at IS NULL
                   OR ck.checked_at < NOW() - (%s * INTERVAL '1 hour'))
            ORDER BY l.cino
            LIMIT %s
        """, (
            current_case_numbers, current_case_numbers,
            current_case_numbers, current_case_numbers,
            bool(force), refresh_hours, max(1, min(int(limit), 100)),
        ))
        return [(str(row[0]), str(row[1])) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def _orders(case_payload: dict[str, Any]) -> list[dict[str, str]]:
    data = case_payload.get("data") or {}
    case_data = data.get("courtCaseData") or {}
    result: list[dict[str, str]] = []
    for field, kind in (("judgmentOrders", "JUDGMENT"), ("interimOrders", "INTERIM")):
        for item in case_data.get(field) or []:
            filename = str(item.get("orderUrl") or "").strip()
            if not filename:
                continue
            result.append({
                "filename": filename.rsplit("/", 1)[-1],
                "order_date": str(item.get("orderDate") or "").strip(),
                "kind": kind,
            })
    # The same order may appear in both arrays.  Filename is authoritative.
    unique: dict[str, dict[str, str]] = {}
    for item in result:
        unique.setdefault(item["filename"], item)
    return list(unique.values())


def _safe_drive_name(cino: str, order: dict[str, str], signed: bool) -> str:
    date_part = re.sub(r"[^0-9-]", "", order.get("order_date", "")) or "undated"
    source = re.sub(r"[^A-Za-z0-9._-]", "-", order["filename"]).strip("-.")
    label = "certified" if signed else "raw"
    return f"eCourts-{cino}-{date_part}-{label}-{source or 'order.pdf'}"[:240]


def _upload_pdf(folder_id: str, name: str, content: bytes) -> tuple[str, str | None]:
    if not content.startswith(b"%PDF"):
        raise RuntimeError("eCourtsIndia response did not contain a valid PDF.")
    drive = get_drive_service()
    if drive is None:
        raise RuntimeError("Google Drive is disconnected.")
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/pdf", resumable=False)
    created = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return str(created["id"]), created.get("webViewLink")


def _claim_order(cur, cino: str, order: dict[str, str], signed: bool) -> bool:
    cur.execute("""
        INSERT INTO ecourts_api_order_downloads
            (cino, order_filename, order_date, order_kind, signed_copy, status)
        VALUES (%s,%s,NULLIF(%s,'')::date,%s,%s,'DISCOVERED')
        ON CONFLICT (cino, order_filename, signed_copy) DO UPDATE SET
            order_date=COALESCE(EXCLUDED.order_date, ecourts_api_order_downloads.order_date),
            order_kind=EXCLUDED.order_kind,
            updated_at=NOW()
        RETURNING status
    """, (cino, order["filename"], order["order_date"], order["kind"], signed))
    return str(cur.fetchone()[0]) in {"DISCOVERED", "FAILED"}


def download_new_orders(
    max_cases: int = 20, max_orders: int = 5, force: bool = False
) -> dict[str, Any]:
    """Download unseen orders for approved CNR links into the Drive inbox."""
    if not api_enabled():
        return {
            "enabled": False, "cases_checked": 0, "orders_found": 0,
            "downloaded": 0, "failed": 0, "results": [],
            "cause_list_count": 0, "eligible_cases": 0,
            "cause_list_date": None, "cause_list_source": None,
        }
    ensure_api_schema()
    # Imported lazily to avoid a circular dependency at module import time.
    from services.ecourts_order_service import _inbox_folder_id

    folder_id = _inbox_folder_id()
    signed = os.getenv("ECOURTSINDIA_SIGNED_PDF", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }
    max_downloads = max(1, min(int(max_orders), 25))
    results: list[dict[str, Any]] = []
    found = downloaded = failed = cases_checked = 0
    current_numbers, cause_count, cause_source, cause_date = (
        _current_advocate_diaries_cases()
    )
    cases = _approved_cases(max_cases, current_numbers, force=force)
    conn = _conn()
    cur = conn.cursor()
    try:
        for cino, case_number in cases:
            if downloaded + failed >= max_downloads:
                break
            cases_checked += 1
            try:
                detail = _request("GET", f"/api/partner/case/{quote(cino)}")
                orders = _orders(detail)
                found += len(orders)
                cur.execute("""
                    INSERT INTO ecourts_api_case_checks (cino, checked_at, order_count, last_error)
                    VALUES (%s,NOW(),%s,NULL)
                    ON CONFLICT (cino) DO UPDATE SET
                        checked_at=NOW(), order_count=EXCLUDED.order_count, last_error=NULL
                """, (cino, len(orders)))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                cur.execute("""
                    INSERT INTO ecourts_api_case_checks (cino, checked_at, order_count, last_error)
                    VALUES (%s,NOW(),0,%s)
                    ON CONFLICT (cino) DO UPDATE SET
                        checked_at=NOW(), last_error=EXCLUDED.last_error
                """, (cino, f"{type(exc).__name__}: {exc}"[:2000]))
                conn.commit()
                failed += 1
                results.append({"cino": cino, "case_number": case_number, "status": "FAILED", "error": str(exc)})
                continue
            for order in orders:
                if downloaded + failed >= max_downloads:
                    break
                if not _claim_order(cur, cino, order, signed):
                    continue
                conn.commit()
                try:
                    flag = "true" if signed else "false"
                    filename = quote(order["filename"], safe="")
                    payload = _request(
                        "GET",
                        f"/api/partner/case/{quote(cino)}/order-md/{filename}?signed={flag}",
                        timeout=max(90, int(os.getenv("ECOURTSINDIA_PDF_TIMEOUT_SECONDS", "330"))),
                    )
                    encoded = str(((payload.get("data") or {}).get("pdfBase64")) or "")
                    if not encoded:
                        raise RuntimeError("eCourtsIndia order response contained no pdfBase64.")
                    content = base64.b64decode(encoded, validate=True)
                    drive_name = _safe_drive_name(cino, order, signed)
                    drive_id, drive_link = _upload_pdf(folder_id, drive_name, content)
                    request_id = str((payload.get("meta") or {}).get("request_id") or "")
                    cur.execute("""
                        UPDATE ecourts_api_order_downloads SET
                            status='DOWNLOADED', drive_file_id=%s, drive_file_link=%s,
                            api_request_id=%s, error_message=NULL,
                            downloaded_at=NOW(), updated_at=NOW()
                        WHERE cino=%s AND order_filename=%s AND signed_copy=%s
                    """, (drive_id, drive_link, request_id, cino, order["filename"], signed))
                    conn.commit()
                    downloaded += 1
                    results.append({
                        "cino": cino, "case_number": case_number,
                        "filename": drive_name, "status": "DOWNLOADED",
                        "drive_link": drive_link,
                    })
                except Exception as exc:
                    conn.rollback()
                    cur.execute("""
                        UPDATE ecourts_api_order_downloads SET
                            status='FAILED', error_message=%s, updated_at=NOW()
                        WHERE cino=%s AND order_filename=%s AND signed_copy=%s
                    """, (f"{type(exc).__name__}: {exc}"[:2000], cino, order["filename"], signed))
                    conn.commit()
                    failed += 1
                    results.append({
                        "cino": cino, "case_number": case_number,
                        "filename": order["filename"], "status": "FAILED",
                        "error": str(exc),
                    })
        return {
            "enabled": True,
            "cause_list_date": cause_date,
            "cause_list_source": cause_source,
            "cause_list_count": cause_count,
            "eligible_cases": len(cases),
            "cases_checked": cases_checked,
            "orders_found": found,
            "downloaded": downloaded,
            "failed": failed,
            "results": results,
        }
    finally:
        cur.close()
        conn.close()
