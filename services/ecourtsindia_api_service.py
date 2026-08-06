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
from datetime import date, datetime
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_api_cause_watch (
                cino TEXT NOT NULL,
                case_number TEXT,
                listing_date DATE NOT NULL,
                source TEXT,
                status TEXT NOT NULL DEFAULT 'WATCHING',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (cino, listing_date)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_api_cause_watch
            ON ecourts_api_cause_watch(status, listing_date, cino)
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


def _same_day_live_mirror(target_date) -> tuple[list[str], int]:
    """Read only that day's Advocate Diaries mirror captured by morning sync."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT to_regclass('public.live_hearings')")
        if not cur.fetchone()[0]:
            return [], 0
        cur.execute("""
            SELECT case_number
            FROM live_hearings
            WHERE hearing_date=%s
              AND COALESCE(UPPER(TRIM(status)), 'LISTED') <> 'DISPOSED'
              AND NULLIF(TRIM(COALESCE(case_number,'')), '') IS NOT NULL
              AND COALESCE(source, '') IN ('PDF', 'API')
            ORDER BY id
        """, (target_date,))
        rows = [str(row[0]) for row in cur.fetchall()]
        numbers = sorted({_compact_case_number(value) for value in rows if _compact_case_number(value)})
        return numbers, len(rows)
    finally:
        cur.close()
        conn.close()


def _current_advocate_diaries_cases() -> tuple[list[str], int, int, str, str]:
    """Return exact case-number keys from today's live Advocate Diaries list.

    This intentionally reads the current source instead of the persistent live
    hearing mirror, which can contain rows imported earlier in the day.
    """
    from commands.dashboard import fetch_advocate_diaries_cause_groups

    today = datetime.now(IST).date()
    groups, source = fetch_advocate_diaries_cause_groups(today)
    matters = [
        case
        for group in groups or []
        for case in group.get("cases", []) or []
    ]
    numbers = {
        _compact_case_number(case.get("case_number"))
        for case in matters
        if _compact_case_number(case.get("case_number"))
    }
    # Advocate Diaries can remove a matter from the daily API as soon as staff
    # records its next date.  At that point use only the same day's morning
    # mirror, never an earlier date or the full historical case database.
    if not matters:
        mirrored_numbers, mirrored_total = _same_day_live_mirror(today)
        if mirrored_total:
            return (
                mirrored_numbers,
                mirrored_total,
                len(mirrored_numbers),
                "SAME-DAY MIRROR",
                today.isoformat(),
            )
    return sorted(numbers), len(matters), len(numbers), str(source), today.isoformat()


def _approved_cases(
    limit: int, current_case_numbers: list[str]
) -> list[tuple[str, str]]:
    if not current_case_numbers:
        return []
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT l.cino,
                   COALESCE(l.local_case_number, c.case_number, c.case_id,
                            b.display_case_number, l.cino)
            FROM ecourts_case_links l
            LEFT JOIN ecourts_backup_records b ON b.cino=l.cino
            LEFT JOIN cases c ON c.id::text=l.local_case_pk
            WHERE l.link_status='APPROVED'
              AND l.cino ~ '^[A-Z]{4}[0-9]{12}$'
              AND COALESCE(UPPER(TRIM(c.status)), 'OPEN') NOT IN ('CLOSED','DISPOSED')
              AND (
                    LOWER(REGEXP_REPLACE(COALESCE(l.local_case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(c.case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(c.case_id,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
                 OR LOWER(REGEXP_REPLACE(COALESCE(b.display_case_number,''), '[^a-zA-Z0-9]', '', 'g')) = ANY(%s)
              )
            ORDER BY l.cino
            LIMIT %s
        """, (
            current_case_numbers, current_case_numbers,
            current_case_numbers, current_case_numbers,
            max(1, min(int(limit), 100)),
        ))
        return [(str(row[0]), str(row[1])) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def _update_cause_watch(
    current_cases: list[tuple[str, str]], listing_date: str, source: str
) -> None:
    if not current_cases:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        for cino, case_number in current_cases:
            cur.execute("""
                INSERT INTO ecourts_api_cause_watch
                    (cino, case_number, listing_date, source, status)
                VALUES (%s,%s,%s,%s,'WATCHING')
                ON CONFLICT (cino, listing_date) DO UPDATE SET
                    case_number=EXCLUDED.case_number, source=EXCLUDED.source,
                    status='WATCHING', updated_at=NOW()
            """, (cino, case_number, listing_date, source))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _backfill_cause_watch(target_date: str) -> int:
    """Seed recent delayed-order watches from stored AD cause-list history."""
    watch_days = max(1, int(os.getenv("ECOURTSINDIA_ORDER_WATCH_DAYS", "30")))
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT to_regclass('public.live_hearings')")
        if not cur.fetchone()[0]:
            return 0
        cur.execute("""
            INSERT INTO ecourts_api_cause_watch
                (cino, case_number, listing_date, source, status)
            SELECT DISTINCT
                l.cino,
                COALESCE(l.local_case_number, c.case_number, c.case_id,
                         b.display_case_number, h.case_number),
                h.hearing_date,
                h.source,
                'WATCHING'
            FROM live_hearings h
            JOIN ecourts_case_links l ON l.link_status='APPROVED'
            LEFT JOIN cases c ON c.id::text=l.local_case_pk
            LEFT JOIN ecourts_backup_records b ON b.cino=l.cino
            WHERE h.hearing_date BETWEEN %s::date - (%s * INTERVAL '1 day') AND %s::date
              AND COALESCE(UPPER(TRIM(h.status)), 'LISTED') <> 'DISPOSED'
              AND COALESCE(h.source, '') IN ('PDF', 'API')
              AND COALESCE(UPPER(TRIM(c.status)), 'OPEN') NOT IN ('CLOSED','DISPOSED')
              AND NULLIF(TRIM(COALESCE(h.case_number,'')), '') IS NOT NULL
              AND (
                    LOWER(REGEXP_REPLACE(COALESCE(h.case_number,''), '[^a-zA-Z0-9]', '', 'g')) =
                    LOWER(REGEXP_REPLACE(COALESCE(l.local_case_number,''), '[^a-zA-Z0-9]', '', 'g'))
                 OR LOWER(REGEXP_REPLACE(COALESCE(h.case_number,''), '[^a-zA-Z0-9]', '', 'g')) =
                    LOWER(REGEXP_REPLACE(COALESCE(c.case_number,''), '[^a-zA-Z0-9]', '', 'g'))
                 OR LOWER(REGEXP_REPLACE(COALESCE(h.case_number,''), '[^a-zA-Z0-9]', '', 'g')) =
                    LOWER(REGEXP_REPLACE(COALESCE(c.case_id,''), '[^a-zA-Z0-9]', '', 'g'))
                 OR LOWER(REGEXP_REPLACE(COALESCE(h.case_number,''), '[^a-zA-Z0-9]', '', 'g')) =
                    LOWER(REGEXP_REPLACE(COALESCE(b.display_case_number,''), '[^a-zA-Z0-9]', '', 'g'))
              )
            ON CONFLICT (cino, listing_date) DO UPDATE SET
                case_number=EXCLUDED.case_number, source=EXCLUDED.source,
                updated_at=NOW()
        """, (target_date, watch_days, target_date))
        affected = max(0, int(cur.rowcount or 0))
        conn.commit()
        return affected
    finally:
        cur.close()
        conn.close()


def _watched_cases(limit: int, force: bool = False) -> list[tuple[str, str, date]]:
    """Return active CNRs listed recently enough for delayed order uploads."""
    watch_days = max(1, int(os.getenv("ECOURTSINDIA_ORDER_WATCH_DAYS", "30")))
    refresh_hours = max(1, int(os.getenv("ECOURTSINDIA_CASE_CHECK_HOURS", "24")))
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT w.cino, MAX(w.case_number), MIN(w.listing_date)
            FROM ecourts_api_cause_watch w
            JOIN ecourts_case_links l
              ON l.cino=w.cino AND l.link_status='APPROVED'
            LEFT JOIN cases c ON c.id::text=l.local_case_pk
            LEFT JOIN ecourts_api_case_checks ck ON ck.cino=w.cino
            WHERE w.status='WATCHING'
              AND w.listing_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
              AND COALESCE(UPPER(TRIM(c.status)), 'OPEN') NOT IN ('CLOSED','DISPOSED')
              AND (%s OR ck.checked_at IS NULL
                   OR ck.checked_at < NOW() - (%s * INTERVAL '1 hour'))
            GROUP BY w.cino, ck.checked_at
            ORDER BY MIN(w.listing_date), w.cino
            LIMIT %s
        """, (
            watch_days, bool(force), refresh_hours,
            max(1, min(int(limit), 100)),
        ))
        return [(str(row[0]), str(row[1]), row[2]) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def _case_status(case_payload: dict[str, Any]) -> str:
    data = case_payload.get("data") or {}
    case_data = data.get("courtCaseData") or {}
    return str(case_data.get("caseStatus") or "").strip().upper()


def _stop_watching(cur, cino: str, status: str) -> None:
    cur.execute("""
        UPDATE ecourts_api_cause_watch
        SET status=%s, updated_at=NOW()
        WHERE cino=%s AND status='WATCHING'
    """, (status, cino))


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


def _orders_since(case_payload: dict[str, Any], watch_start: date) -> list[dict[str, str]]:
    """Exclude historical orders from before this CNR entered the cause watch."""
    eligible: list[dict[str, str]] = []
    for order in _orders(case_payload):
        try:
            order_date = date.fromisoformat(order.get("order_date") or "")
        except ValueError:
            # Unknown-date PDFs cannot be safely distinguished from old files.
            continue
        if order_date >= watch_start:
            eligible.append(order)
    return eligible


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
            "cause_list_total": 0,
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
    current_numbers, cause_total, cause_count, cause_source, cause_date = (
        _current_advocate_diaries_cases()
    )
    current_cases = _approved_cases(100, current_numbers)
    _update_cause_watch(current_cases, cause_date, cause_source)
    backfilled = _backfill_cause_watch(cause_date)
    cases = _watched_cases(max_cases, force=force)
    conn = _conn()
    cur = conn.cursor()
    try:
        for cino, case_number, watch_start in cases:
            if downloaded + failed >= max_downloads:
                break
            cases_checked += 1
            try:
                detail = _request("GET", f"/api/partner/case/{quote(cino)}")
                remote_status = _case_status(detail)
                if remote_status in {"DISPOSED", "DISMISSED", "CLOSED"}:
                    _stop_watching(cur, cino, "DISPOSED")
                    cur.execute("""
                        INSERT INTO ecourts_api_case_checks
                            (cino, checked_at, order_count, last_error)
                        VALUES (%s,NOW(),0,NULL)
                        ON CONFLICT (cino) DO UPDATE SET
                            checked_at=NOW(), last_error=NULL
                    """, (cino,))
                    conn.commit()
                    results.append({
                        "cino": cino, "case_number": case_number,
                        "status": "SKIPPED_DISPOSED",
                    })
                    continue
                orders = _orders_since(detail, watch_start)
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
            "cause_list_total": cause_total,
            "eligible_cases": len(cases),
            "new_cause_watch_entries": len(current_cases),
            "cause_watch_backfilled": backfilled,
            "cases_checked": cases_checked,
            "orders_found": found,
            "downloaded": downloaded,
            "failed": failed,
            "results": results,
        }
    finally:
        cur.close()
        conn.close()
