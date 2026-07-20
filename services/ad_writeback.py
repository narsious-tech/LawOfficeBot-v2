from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg2
import requests

from advocate_diaries import AdvocateDiaries, BASE_URL
from config import DATABASE_URL


@dataclass
class WritebackResult:
    status: str
    message: str
    remote_case_id: str | None = None
    endpoint: str | None = None


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-ad-writeback")


def ensure_sync_queue() -> None:
    conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS advocate_diaries_sync_queue (
            id SERIAL PRIMARY KEY,
            live_hearing_id INTEGER,
            case_number TEXT NOT NULL,
            remote_case_id TEXT,
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_sync_queue_status ON advocate_diaries_sync_queue(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_sync_queue_case ON advocate_diaries_sync_queue(case_number)")
        conn.commit()
    finally:
        cur.close(); conn.close()


def queue_writeback(live_hearing_id: int, case_number: str, payload: dict[str, Any], error: str, remote_case_id: str | None = None) -> int:
    ensure_sync_queue(); conn = _connect(); cur = conn.cursor()
    try:
        cur.execute("""
        INSERT INTO advocate_diaries_sync_queue(live_hearing_id,case_number,remote_case_id,payload,status,attempts,last_error,next_retry_at)
        VALUES (%s,%s,%s,%s::jsonb,'PENDING',1,%s,CURRENT_TIMESTAMP + INTERVAL '15 minutes')
        RETURNING id
        """, (live_hearing_id, case_number, remote_case_id, json.dumps(payload), error[:2000]))
        queue_id = cur.fetchone()[0]; conn.commit(); return queue_id
    finally:
        cur.close(); conn.close()


def _extract_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "results", "records", "court_cases", "cases", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for nested in ("data", "results", "records", "items"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return [x for x in rows if isinstance(x, dict)]
    return []


def _case_number(item: dict[str, Any]) -> str:
    for key in ("case_number", "case_no", "caseNumber", "number"):
        if item.get(key):
            return str(item[key]).strip()
    return ""


def find_remote_case(case_number: str) -> tuple[str | None, dict[str, Any] | None]:
    client = AdvocateDiaries()
    response = requests.get(
        f"{BASE_URL}/court_cases",
        params={"search": case_number},
        headers=client.headers(),
        timeout=(10, 60),
    )
    response.raise_for_status()
    data = response.json()
    normalized = case_number.replace(" ", "").lower()
    for item in _extract_records(data):
        candidate = _case_number(item).replace(" ", "").lower()
        if candidate == normalized:
            remote_id = item.get("id") or item.get("court_case_id") or item.get("case_id") or item.get("uuid")
            return (str(remote_id) if remote_id is not None else None), item
    return None, None


def _build_payload(next_date: date | None, next_purpose: str, order_summary: str, documents_required: str) -> dict[str, Any]:
    # Canonical payload. Field aliases can be overridden in Railway without code changes.
    date_key = os.getenv("AD_UPDATE_DATE_FIELD", "next_hearing")
    purpose_key = os.getenv("AD_UPDATE_PURPOSE_FIELD", "purpose")
    order_key = os.getenv("AD_UPDATE_ORDER_FIELD", "order_summary")
    prep_key = os.getenv("AD_UPDATE_PREPARATION_FIELD", "notes")
    payload: dict[str, Any] = {
        date_key: next_date.isoformat() if next_date else None,
        purpose_key: next_purpose or None,
        order_key: order_summary or None,
        prep_key: documents_required or None,
    }
    return {k: v for k, v in payload.items() if v is not None}


def writeback_hearing(
    *, live_hearing_id: int, case_number: str, next_date: date | None,
    next_purpose: str, order_summary: str, documents_required: str,
) -> WritebackResult:
    ensure_sync_queue()
    payload = _build_payload(next_date, next_purpose, order_summary, documents_required)
    try:
        remote_id, _ = find_remote_case(case_number)
        if not remote_id:
            qid = queue_writeback(live_hearing_id, case_number, payload, "Matching Advocate Diaries case ID not found")
            return WritebackResult("QUEUED", f"Matching Advocate Diaries case not found; queued as #{qid}.")

        endpoint_template = os.getenv("AD_CASE_UPDATE_ENDPOINT", "/court_cases/{id}")
        endpoint = endpoint_template.format(id=remote_id)
        if not endpoint.startswith("http"):
            endpoint = f"{BASE_URL}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
        method = os.getenv("AD_CASE_UPDATE_METHOD", "PATCH").upper()
        client = AdvocateDiaries()
        response = requests.request(method, endpoint, json=payload, headers=client.headers(), timeout=(10, 60))
        if response.status_code >= 400:
            detail = response.text[:1000]
            raise RuntimeError(f"HTTP {response.status_code}: {detail}")
        return WritebackResult("SUCCESS", "Advocate Diaries updated.", remote_case_id=remote_id, endpoint=endpoint)
    except Exception as exc:
        qid = queue_writeback(live_hearing_id, case_number, payload, f"{type(exc).__name__}: {exc}")
        return WritebackResult("QUEUED", f"Write-back queued as #{qid}: {type(exc).__name__}: {exc}")


def retry_pending(limit: int = 20) -> dict[str, int]:
    ensure_sync_queue(); conn = _connect(); cur = conn.cursor()
    stats = {"processed": 0, "success": 0, "failed": 0}
    try:
        cur.execute("""
        SELECT id,live_hearing_id,case_number,payload FROM advocate_diaries_sync_queue
        WHERE status='PENDING' AND (next_retry_at IS NULL OR next_retry_at<=CURRENT_TIMESTAMP)
        ORDER BY id LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        for queue_id, live_id, case_number, payload in rows:
            stats["processed"] += 1
            try:
                remote_id, _ = find_remote_case(case_number)
                if not remote_id:
                    raise RuntimeError("Matching case ID not found")
                endpoint_template = os.getenv("AD_CASE_UPDATE_ENDPOINT", "/court_cases/{id}")
                endpoint = endpoint_template.format(id=remote_id)
                if not endpoint.startswith("http"):
                    endpoint = f"{BASE_URL}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
                method = os.getenv("AD_CASE_UPDATE_METHOD", "PATCH").upper()
                client = AdvocateDiaries()
                r = requests.request(method, endpoint, json=payload, headers=client.headers(), timeout=(10,60))
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                cur.execute("UPDATE advocate_diaries_sync_queue SET status='SUCCESS',remote_case_id=%s,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=%s", (remote_id, queue_id))
                conn.commit(); stats["success"] += 1
            except Exception as exc:
                cur.execute("""
                    UPDATE advocate_diaries_sync_queue SET attempts=attempts+1,last_error=%s,
                    next_retry_at=CURRENT_TIMESTAMP + INTERVAL '30 minutes',updated_at=CURRENT_TIMESTAMP
                    WHERE id=%s
                """, (f"{type(exc).__name__}: {exc}"[:2000], queue_id))
                conn.commit(); stats["failed"] += 1
        return stats
    finally:
        cur.close(); conn.close()
