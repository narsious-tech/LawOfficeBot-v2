"""Google Drive order inbox, case matching, archiving and AI working notes."""
from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime, timezone
from typing import Any

import psycopg2
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

from ai.config import AIConfig
from ai.gateway import AIGateway, AIUnavailable
from ai.schema import ensure_ai_schema
from ai.session_store import AISessionStore
from config import DATABASE_URL
from utils.drive import (
    ROOT_FOLDER_ID,
    get_drive_service,
    get_or_create_case_folder,
    get_or_create_subfolder,
)


def _conn():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="law-office-ecourts-order-inbox",
    )


def ensure_order_schema() -> None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_order_inbox (
                id BIGSERIAL PRIMARY KEY,
                drive_file_id TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL,
                original_link TEXT,
                modified_time TIMESTAMPTZ,
                sha256_hash TEXT,
                cino TEXT,
                local_case_pk TEXT,
                case_number TEXT,
                order_date DATE,
                processing_status TEXT NOT NULL DEFAULT 'NEW',
                importance TEXT NOT NULL DEFAULT 'NORMAL',
                extracted_text TEXT,
                ai_summary TEXT,
                archived_drive_file_id TEXT,
                archived_drive_link TEXT,
                error_message TEXT,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ,
                alerted_at TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_order_status
            ON ecourts_order_inbox(processing_status, id DESC)
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _inbox_folder_id() -> str:
    configured = os.getenv("ECOURTS_ORDER_INBOX_FOLDER_ID", "").strip()
    if configured:
        return configured
    folder_id, _ = get_or_create_subfolder(ROOT_FOLDER_ID, "eCourts Order Inbox")
    return folder_id


def _download(file_id: str) -> bytes:
    drive = get_drive_service()
    if drive is None:
        raise RuntimeError("Google Drive is disconnected.")
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    output = io.BytesIO()
    downloader = MediaIoBaseDownload(output, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return output.getvalue()


def _extract_pdf_text(content: bytes, max_pages: int = 80) -> str:
    reader = PdfReader(io.BytesIO(content))
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks).strip()[:180000]


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _match_case(cur, file_name: str, text: str) -> dict[str, Any] | None:
    haystack = f"{file_name}\n{text}".upper()
    compact = _compact(haystack)
    cnrs = re.findall(r"\b[A-Z]{4}\d{12}\b", haystack)
    for cino in cnrs:
        cur.execute("""
            SELECT b.cino, b.display_case_number, l.local_case_pk
            FROM ecourts_backup_records b
            LEFT JOIN ecourts_case_links l
              ON l.cino=b.cino AND l.link_status='APPROVED'
            WHERE b.cino=%s
        """, (cino,))
        row = cur.fetchone()
        if row:
            return {"cino": row[0], "case_number": row[1], "local_case_pk": row[2]}

    cur.execute("""
        SELECT b.cino, b.display_case_number, l.local_case_pk
        FROM ecourts_backup_records b
        LEFT JOIN ecourts_case_links l
          ON l.cino=b.cino AND l.link_status='APPROVED'
        WHERE b.display_case_number IS NOT NULL
    """)
    candidates = []
    for cino, case_number, local_pk in cur.fetchall():
        token = _compact(case_number)
        if token and len(token) >= 7 and token in compact:
            candidates.append({
                "cino": cino, "case_number": case_number,
                "local_case_pk": local_pk, "token_length": len(token),
            })
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["token_length"], reverse=True)
    return candidates[0]


IMPORTANT_PATTERNS = {
    "CRITICAL": (
        "stay granted", "stay vacated", "injunction granted", "injunction vacated",
        "bail granted", "bail rejected", "bail dismissed", "disposed of",
        "petition dismissed", "petition allowed", "evidence closed",
        "non-bailable warrant", "nbw", "proclaimed offender",
    ),
    "IMPORTANT": (
        "last opportunity", "subject to costs", "costs of", "notice issued",
        "summons issued", "warrant", "order reserved", "judgment reserved",
        "file reply", "file affidavit", "compliance", "personal appearance",
    ),
}


def _importance(text: str) -> str:
    lowered = text.casefold()
    for level in ("CRITICAL", "IMPORTANT"):
        if any(term in lowered for term in IMPORTANT_PATTERNS[level]):
            return level
    return "NORMAL"


def _admin_ai_user_id() -> int | None:
    values = (
        os.getenv("AI_ADMIN_USER_IDS", ""),
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    )
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item.isdigit():
                return int(item)
    return None


def _ai_summary(text: str, case_number: str, cino: str) -> str | None:
    if os.getenv("ECOURTS_ORDER_AI_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    config = AIConfig.from_env()
    user_id = _admin_ai_user_id()
    if not config.enabled or not config.api_key or user_id is None or not text:
        return None
    ensure_ai_schema()
    store = AISessionStore()
    session_id = store.create_session(user_id, "order_intelligence", case_number)
    request = (
        "Prepare a reviewable working note from this judicial order. Extract only "
        "directions actually present in the text, all dates/deadlines, the result, "
        "and recommended office actions. Clearly say when text is incomplete."
    )
    store.add_message(session_id, "user", request)
    result = AIGateway(config=config, store=store).generate(
        user_id=user_id,
        session_id=session_id,
        user_text=request,
        feature="order_intelligence",
        office_context=(
            f"CASE NUMBER: {case_number}\nCNR: {cino}\n"
            f"VERIFIED EXTRACTED ORDER TEXT:\n{text[:90000]}"
        ),
    )
    store.add_message(session_id, "assistant", result.text)
    return result.text


def _archive_copy(
    drive_file_id: str, original_name: str, local_case_pk: str, case_number: str
) -> tuple[str | None, str | None]:
    drive = get_drive_service()
    if drive is None:
        raise RuntimeError("Google Drive is disconnected.")
    cur_conn = _conn()
    cur = cur_conn.cursor()
    try:
        cur.execute("""
            SELECT drive_folder_id FROM cases WHERE id::text=%s
        """, (str(local_case_pk),))
        row = cur.fetchone()
        folder_id = row[0] if row and row[0] else None
    finally:
        cur.close()
        cur_conn.close()
    if not folder_id:
        folder_id, _ = get_or_create_case_folder(case_number)
    orders_folder, _ = get_or_create_subfolder(folder_id, "Orders & Judgments")
    copied = drive.files().copy(
        fileId=drive_file_id,
        body={"name": original_name, "parents": [orders_folder]},
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return copied.get("id"), copied.get("webViewLink")


def scan_order_inbox(max_files: int = 10, retry_unmatched: bool = False) -> dict[str, Any]:
    ensure_order_schema()
    drive = get_drive_service()
    if drive is None:
        raise RuntimeError("Google Drive is disconnected.")
    inbox_id = _inbox_folder_id()
    response = drive.files().list(
        q=(
            f"'{inbox_id}' in parents and trashed=false and "
            "mimeType='application/pdf'"
        ),
        fields="files(id,name,webViewLink,modifiedTime,size)",
        orderBy="modifiedTime desc",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = response.get("files", [])
    conn = _conn()
    cur = conn.cursor()
    results: list[dict[str, Any]] = []
    processed = 0
    try:
        for item in files:
            if processed >= max(1, min(int(max_files), 25)):
                break
            cur.execute(
                "SELECT processing_status, extracted_text FROM ecourts_order_inbox WHERE drive_file_id=%s",
                (item["id"],),
            )
            existing = cur.fetchone()
            if existing:
                retryable = existing[0] in {"UNMATCHED", "FAILED"}
                if not retry_unmatched or not retryable:
                    continue
            processed += 1
            try:
                content = _download(item["id"])
                digest = hashlib.sha256(content).hexdigest()
                cur.execute("""
                    SELECT cino, case_number, archived_drive_link
                    FROM ecourts_order_inbox
                    WHERE sha256_hash=%s AND drive_file_id<>%s
                      AND processing_status IN ('MATCHED','ARCHIVED','DUPLICATE')
                    ORDER BY id LIMIT 1
                """, (digest, item["id"]))
                duplicate = cur.fetchone()
                if duplicate:
                    cur.execute("""
                        INSERT INTO ecourts_order_inbox (
                            drive_file_id, original_name, original_link, modified_time,
                            sha256_hash, cino, case_number, processing_status,
                            importance, archived_drive_link, processed_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,'DUPLICATE','NORMAL',%s,NOW())
                        ON CONFLICT (drive_file_id) DO UPDATE SET
                            processing_status='DUPLICATE', sha256_hash=EXCLUDED.sha256_hash,
                            cino=EXCLUDED.cino, case_number=EXCLUDED.case_number,
                            archived_drive_link=EXCLUDED.archived_drive_link,
                            processed_at=NOW(), error_message=NULL
                        RETURNING id
                    """, (
                        item["id"], item.get("name") or "Unnamed order.pdf",
                        item.get("webViewLink"), item.get("modifiedTime"), digest,
                        duplicate[0], duplicate[1], duplicate[2],
                    ))
                    record_id = int(cur.fetchone()[0])
                    conn.commit()
                    results.append({
                        "id": record_id, "name": item.get("name"),
                        "status": "DUPLICATE", "importance": "NORMAL",
                        "case_number": duplicate[1], "cino": duplicate[0],
                        "original_link": item.get("webViewLink"),
                        "archived_link": duplicate[2], "ai_summary": None,
                    })
                    continue
                text = existing[1] if existing and existing[1] else _extract_pdf_text(content)
                match = _match_case(cur, item.get("name", ""), text)
                if not match:
                    status, importance = "UNMATCHED", _importance(text)
                    summary = None
                    archived_id = archived_link = None
                else:
                    status, importance = "MATCHED", _importance(text)
                    archived_id = archived_link = None
                    if match.get("local_case_pk"):
                        archived_id, archived_link = _archive_copy(
                            item["id"], item.get("name") or "eCourts-order.pdf",
                            str(match["local_case_pk"]), match["case_number"],
                        )
                        status = "ARCHIVED"
                    try:
                        summary = _ai_summary(
                            text, match["case_number"], match["cino"]
                        )
                    except Exception:
                        summary = None
                modified = item.get("modifiedTime")
                cur.execute("""
                    INSERT INTO ecourts_order_inbox (
                        drive_file_id, original_name, original_link, modified_time,
                        sha256_hash, cino, local_case_pk, case_number,
                        processing_status, importance, extracted_text, ai_summary,
                        archived_drive_file_id, archived_drive_link,
                        processed_at, error_message
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NULL)
                    ON CONFLICT (drive_file_id) DO UPDATE SET
                        sha256_hash=EXCLUDED.sha256_hash,
                        cino=EXCLUDED.cino,
                        local_case_pk=EXCLUDED.local_case_pk,
                        case_number=EXCLUDED.case_number,
                        processing_status=EXCLUDED.processing_status,
                        importance=EXCLUDED.importance,
                        extracted_text=EXCLUDED.extracted_text,
                        ai_summary=EXCLUDED.ai_summary,
                        archived_drive_file_id=EXCLUDED.archived_drive_file_id,
                        archived_drive_link=EXCLUDED.archived_drive_link,
                        processed_at=NOW(), error_message=NULL
                    RETURNING id
                """, (
                    item["id"], item.get("name") or "Unnamed order.pdf",
                    item.get("webViewLink"), modified, digest,
                    match.get("cino") if match else None,
                    match.get("local_case_pk") if match else None,
                    match.get("case_number") if match else None,
                    status, importance, text, summary, archived_id, archived_link,
                ))
                record_id = int(cur.fetchone()[0])
                results.append({
                    "id": record_id, "name": item.get("name"),
                    "status": status, "importance": importance,
                    "case_number": match.get("case_number") if match else None,
                    "cino": match.get("cino") if match else None,
                    "original_link": item.get("webViewLink"),
                    "archived_link": archived_link, "ai_summary": summary,
                })
                conn.commit()
            except Exception as exc:
                conn.rollback()
                cur.execute("""
                    INSERT INTO ecourts_order_inbox (
                        drive_file_id, original_name, original_link,
                        processing_status, error_message, processed_at
                    ) VALUES (%s,%s,%s,'FAILED',%s,NOW())
                    ON CONFLICT (drive_file_id) DO UPDATE SET
                        processing_status='FAILED', error_message=EXCLUDED.error_message,
                        processed_at=NOW()
                """, (
                    item["id"], item.get("name") or "Unnamed order.pdf",
                    item.get("webViewLink"), f"{type(exc).__name__}: {exc}"[:2000],
                ))
                conn.commit()
                results.append({
                    "name": item.get("name"), "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return {
            "inbox_folder_id": inbox_id,
            "files_seen": len(files),
            "processed_count": len(results),
            "results": results,
        }
    finally:
        cur.close()
        conn.close()


def list_orders(limit: int = 30, only_unalerted: bool = False) -> list[dict[str, Any]]:
    ensure_order_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        where = (
            "WHERE alerted_at IS NULL AND processing_status<>'DUPLICATE'"
            if only_unalerted else ""
        )
        cur.execute(f"""
            SELECT id, original_name, original_link, cino, case_number,
                   processing_status, importance, ai_summary,
                   archived_drive_link, processed_at, alerted_at, error_message
            FROM ecourts_order_inbox
            {where}
            ORDER BY id DESC LIMIT %s
        """, (max(1, min(int(limit), 100)),))
        names = [column.name for column in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def mark_orders_alerted(ids: list[int]) -> None:
    if not ids:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE ecourts_order_inbox SET alerted_at=NOW() WHERE id=ANY(%s)",
            ([int(item) for item in ids],),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
