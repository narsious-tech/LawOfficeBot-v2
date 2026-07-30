"""Sprint 25.6: approved eCourts updates, AD date sync and AI work proposals."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ai.config import AIConfig
from ai.gateway import AIGateway
from ai.schema import ensure_ai_schema
from ai.session_store import AISessionStore
from config import DATABASE_URL


def _conn():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="law-office-ecourts-orchestration",
    )


def ensure_orchestration_schema() -> None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_ad_sync_events (
                id BIGSERIAL PRIMARY KEY,
                preparation_queue_id BIGINT NOT NULL UNIQUE,
                local_case_pk TEXT NOT NULL,
                cino TEXT NOT NULL,
                case_number TEXT NOT NULL,
                hearing_date DATE,
                next_hearing_date DATE,
                next_purpose TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                message TEXT,
                remote_case_id TEXT,
                verified BOOLEAN NOT NULL DEFAULT FALSE,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_ai_work_proposals (
                id BIGSERIAL PRIMARY KEY,
                order_inbox_id BIGINT NOT NULL UNIQUE,
                local_case_pk TEXT NOT NULL,
                cino TEXT,
                case_number TEXT NOT NULL,
                assigned_to TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT,
                priority TEXT NOT NULL DEFAULT 'NORMAL',
                due_date DATE,
                proposal_status TEXT NOT NULL DEFAULT 'PENDING_ADMIN',
                generation_mode TEXT NOT NULL DEFAULT 'AI',
                ai_raw_response TEXT,
                case_work_id BIGINT,
                reviewed_by BIGINT,
                reviewed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute(
            "ALTER TABLE case_works ADD COLUMN IF NOT EXISTS external_source_id TEXT"
        )
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_case_works_external_source
            ON case_works(source, external_source_id)
            WHERE external_source_id IS NOT NULL
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _india_today() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _ad_write_safety_error(
    *,
    next_date: date | None,
    review_decision: str | None,
    verification_status: str | None,
) -> tuple[str, str] | None:
    """Refuse unapproved or historical eCourts dates before any AD request."""
    if (
        str(review_decision or "").upper() != "ACCEPT_ECOURTS"
        or str(verification_status or "").upper() != "ECOURTS_ACCEPTED"
    ):
        return (
            "BLOCKED_NOT_APPROVED",
            "Advocate Diaries write blocked: no valid administrator acceptance was found.",
        )
    if next_date and next_date < _india_today():
        return (
            "HISTORICAL_SKIPPED",
            "Advocate Diaries write blocked: the proposed next hearing date is historical.",
        )
    return None


def sync_approved_case_to_ad(
    sync_run_id: int, cino: str, actor_id: int
) -> dict[str, Any]:
    """Push approved dates to AD after the local transaction has committed."""
    ensure_orchestration_schema()
    normalized_cino = str(cino or "").strip().upper()
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT q.id preparation_queue_id, q.local_case_pk, q.cino,
                   q.next_hearing_date, q.purpose_name,
                   COALESCE(c.case_number, c.case_id) case_number,
                   c.last_hearing_date, c.next_purpose, c.ad_case_id,
                   v.ecourts_last_date, v.staff_last_date,
                   v.ecourts_next_date, v.review_decision,
                   v.verification_status, v.reviewed_at
            FROM ecourts_preparation_queue q
            JOIN cases c ON c.id::text=q.local_case_pk
            LEFT JOIN ecourts_date_verifications v
              ON v.local_case_pk=q.local_case_pk AND v.cino=q.cino
            WHERE q.source_sync_run_id=%s AND q.cino=%s
            ORDER BY q.id DESC LIMIT 1
        """, (int(sync_run_id), normalized_cino))
        row = cur.fetchone()
        if not row:
            return {"status": "SKIPPED", "message": "No approved preparation record was found."}
        data = dict(row)
        cur.execute("""
            SELECT status, message, verified, remote_case_id
            FROM ecourts_ad_sync_events WHERE preparation_queue_id=%s
        """, (data["preparation_queue_id"],))
        existing = cur.fetchone()
        if existing and existing["status"] == "SUCCESS":
            return {
                "status": "ALREADY_SYNCED", "message": existing["message"],
                "verified": existing["verified"],
                "remote_case_id": existing["remote_case_id"],
            }
        hearing_date = (
            _as_date(data.get("ecourts_last_date"))
            or _as_date(data.get("staff_last_date"))
            or _as_date(data.get("last_hearing_date"))
        )
        next_date = (
            _as_date(data.get("next_hearing_date"))
            or _as_date(data.get("ecourts_next_date"))
        )
        safety_error = _ad_write_safety_error(
            next_date=next_date,
            review_decision=data.get("review_decision"),
            verification_status=data.get("verification_status"),
        )
        if not hearing_date or not next_date or safety_error:
            if safety_error:
                status, message = safety_error
            else:
                status, message = (
                    "INVALID_DATE_SKIPPED",
                    "Approved update lacks a usable last or next hearing date.",
                )
            cur.execute("""
                INSERT INTO ecourts_ad_sync_events (
                    preparation_queue_id,local_case_pk,cino,case_number,
                    hearing_date,next_hearing_date,next_purpose,status,message,attempts
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT (preparation_queue_id) DO UPDATE SET
                    status=EXCLUDED.status,message=EXCLUDED.message,
                    attempts=ecourts_ad_sync_events.attempts+1,updated_at=NOW()
            """, (
                data["preparation_queue_id"], data["local_case_pk"], normalized_cino,
                data["case_number"], hearing_date, next_date,
                data.get("purpose_name") or data.get("next_purpose"),
                status, message,
            ))
            conn.commit()
            return {"status": status, "message": message}
        conn.commit()
    finally:
        cur.close()
        conn.close()

    from services.ad_writeback import writeback_hearing
    result = writeback_hearing(
        live_hearing_id=0,
        case_number=str(data["case_number"]),
        remote_case_id=str(data.get("ad_case_id") or "").strip() or None,
        hearing_date=hearing_date,
        next_date=next_date,
        next_purpose=str(data.get("purpose_name") or data.get("next_purpose") or ""),
        order_summary="Hearing dates synchronized after administrator-approved eCourts update.",
        documents_required="",
    )
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO ecourts_ad_sync_events (
                preparation_queue_id,local_case_pk,cino,case_number,
                hearing_date,next_hearing_date,next_purpose,status,message,
                remote_case_id,verified,attempts
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            ON CONFLICT (preparation_queue_id) DO UPDATE SET
                status=EXCLUDED.status,message=EXCLUDED.message,
                remote_case_id=EXCLUDED.remote_case_id,verified=EXCLUDED.verified,
                attempts=ecourts_ad_sync_events.attempts+1,updated_at=NOW()
        """, (
            data["preparation_queue_id"], data["local_case_pk"], normalized_cino,
            data["case_number"], hearing_date, next_date,
            data.get("purpose_name") or data.get("next_purpose"),
            result.status, result.message, result.remote_case_id, result.verified,
        ))
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit
                (action,local_case_pk,cino,details,actor_id)
            VALUES ('AD_DATE_SYNC',%s,%s,%s,%s)
        """, (
            data["local_case_pk"], normalized_cino,
            Json({"status": result.status, "message": result.message,
                  "verified": result.verified}), int(actor_id),
        ))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return {
        "status": result.status, "message": result.message,
        "verified": result.verified, "remote_case_id": result.remote_case_id,
    }


def retry_pending_ecourts_ad_syncs(limit: int = 20) -> dict[str, int]:
    """Recover only recent, approved, future-dated AD hand-offs."""
    ensure_orchestration_schema()
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT DISTINCT q.source_sync_run_id, q.cino
            FROM ecourts_ad_sync_events e
            JOIN ecourts_preparation_queue q
              ON q.id=e.preparation_queue_id
            JOIN ecourts_date_verifications v
              ON v.local_case_pk=q.local_case_pk AND v.cino=q.cino
            WHERE e.status IN ('SKIPPED','QUEUED','FAILED')
              AND v.review_decision='ACCEPT_ECOURTS'
              AND v.verification_status='ECOURTS_ACCEPTED'
              AND v.reviewed_at >= NOW() - INTERVAL '7 days'
              AND e.updated_at >= NOW() - INTERVAL '7 days'
              AND COALESCE(q.next_hearing_date,v.ecourts_next_date) >= CURRENT_DATE
            ORDER BY q.source_sync_run_id, q.cino
            LIMIT %s
        """, (int(limit),))
        candidates = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    stats = {"processed": 0, "success": 0, "pending": 0}
    for candidate in candidates:
        stats["processed"] += 1
        try:
            result = sync_approved_case_to_ad(
                int(candidate["source_sync_run_id"]),
                str(candidate["cino"]),
                0,
            )
            if result.get("status") in {"SUCCESS", "ALREADY_SYNCED"}:
                stats["success"] += 1
            else:
                stats["pending"] += 1
        except Exception:
            stats["pending"] += 1
    return stats


def _admin_ai_user_id() -> int | None:
    for name in ("AI_ADMIN_USER_IDS", "ADMIN_USER_ID", "ADMIN_CHAT_ID"):
        for value in os.getenv(name, "").split(","):
            value = value.strip()
            if value.isdigit():
                return int(value)
    return None


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        raise ValueError("AI response did not contain a JSON object.")
    return json.loads(match.group(0))


def _owner(cur, case_number: str, local_case_pk: str) -> str:
    cur.execute("""
        SELECT owner_staff FROM case_ownership
        WHERE active=TRUE AND (
            LOWER(TRIM(case_number))=LOWER(TRIM(%s)) OR case_record_id::text=%s
        )
        ORDER BY manual_override DESC, updated_at DESC LIMIT 1
    """, (case_number, local_case_pk))
    row = cur.fetchone()
    return str(row[0]).strip() if row and row[0] else "Preet"


def generate_order_work_proposals(limit: int = 10) -> list[dict[str, Any]]:
    """Create admin-reviewable proposals; never assigns work automatically."""
    ensure_orchestration_schema()
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT o.id order_inbox_id,o.local_case_pk,o.cino,o.case_number,
                   o.extracted_text,o.ai_summary,o.importance,
                   c.next_hearing,c.hearing_date
            FROM ecourts_order_inbox o
            JOIN cases c ON c.id::text=o.local_case_pk
            LEFT JOIN ecourts_ai_work_proposals p ON p.order_inbox_id=o.id
            WHERE o.processing_status IN ('MATCHED','ARCHIVED')
              AND o.local_case_pk IS NOT NULL
              AND p.id IS NULL
            ORDER BY o.id DESC LIMIT %s
        """, (max(1, min(int(limit), 25)),))
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    created: list[dict[str, Any]] = []
    for row in rows:
        owner = "Preet"
        proposal: dict[str, Any]
        raw = ""
        mode = "AI"
        conn = _conn()
        cur = conn.cursor()
        try:
            owner = _owner(cur, str(row["case_number"]), str(row["local_case_pk"]))
        finally:
            cur.close()
            conn.close()
        try:
            config = AIConfig.from_env()
            user_id = _admin_ai_user_id()
            if not user_id or not config.enabled or not config.api_key:
                raise RuntimeError("AI is not configured.")
            ensure_ai_schema()
            store = AISessionStore()
            session_id = store.create_session(
                user_id, "ecourts_work_proposal", str(row["case_number"])
            )
            request = (
                "Create one proposed office work item from the verified order text. "
                "Return only the required JSON."
            )
            store.add_message(session_id, "user", request)
            result = AIGateway(config=config, store=store).generate(
                user_id=user_id,
                session_id=session_id,
                user_text=request,
                feature="ecourts_work_proposal",
                office_context=(
                    f"CASE: {row['case_number']}\nCNR: {row.get('cino') or '-'}\n"
                    f"NEXT HEARING: {row.get('next_hearing') or row.get('hearing_date') or '-'}\n"
                    f"ORDER IMPORTANCE: {row.get('importance') or 'NORMAL'}\n"
                    f"EXISTING AI NOTE:\n{row.get('ai_summary') or '-'}\n"
                    f"VERIFIED ORDER TEXT:\n{str(row.get('extracted_text') or '')[:90000]}"
                ),
            )
            raw = result.text
            proposal = _parse_json_object(raw)
        except Exception as exc:
            mode = "SAFE_FALLBACK"
            raw = f"{type(exc).__name__}: {exc}"
            proposal = {
                "title": "Review interim order manually",
                "details": str(row.get("ai_summary") or "Read the archived order and record operative directions."),
                "priority": "HIGH" if row.get("importance") in {"CRITICAL", "IMPORTANT"} else "NORMAL",
                "due_date": None,
                "reason": "AI proposal unavailable; manual legal review required.",
            }
        due = _as_date(proposal.get("due_date"))
        next_date = _as_date(row.get("next_hearing") or row.get("hearing_date"))
        if due is None and next_date:
            due = max(date.today(), next_date - timedelta(days=3))
        priority = str(proposal.get("priority") or "NORMAL").upper()
        if priority not in {"URGENT", "HIGH", "NORMAL", "LOW"}:
            priority = "NORMAL"
        conn = _conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("""
                INSERT INTO ecourts_ai_work_proposals (
                    order_inbox_id,local_case_pk,cino,case_number,assigned_to,
                    title,details,priority,due_date,generation_mode,ai_raw_response
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (order_inbox_id) DO NOTHING
                RETURNING *
            """, (
                row["order_inbox_id"], row["local_case_pk"], row.get("cino"),
                row["case_number"], owner,
                str(proposal.get("title") or "Review interim order manually")[:500],
                str(proposal.get("details") or proposal.get("reason") or "")[:10000],
                priority, due, mode, raw[:20000],
            ))
            inserted = cur.fetchone()
            conn.commit()
            if inserted:
                created.append(dict(inserted))
        finally:
            cur.close()
            conn.close()
    return created


def list_work_proposals(limit: int = 20, status: str = "PENDING_ADMIN") -> list[dict[str, Any]]:
    ensure_orchestration_schema()
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM ecourts_ai_work_proposals
            WHERE proposal_status=%s ORDER BY id LIMIT %s
        """, (status, max(1, min(int(limit), 100))))
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def review_work_proposal(proposal_id: int, decision: str, actor_id: int) -> dict[str, Any]:
    ensure_orchestration_schema()
    decision = str(decision or "").upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("Invalid work-proposal decision.")
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM ecourts_ai_work_proposals WHERE id=%s FOR UPDATE",
            (int(proposal_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Work proposal was not found.")
        data = dict(row)
        if data["proposal_status"] != "PENDING_ADMIN":
            conn.commit()
            data["already_reviewed"] = True
            return data
        if decision == "REJECT":
            cur.execute("""
                UPDATE ecourts_ai_work_proposals
                SET proposal_status='REJECTED',reviewed_by=%s,reviewed_at=NOW()
                WHERE id=%s RETURNING *
            """, (int(actor_id), int(proposal_id)))
            result = dict(cur.fetchone())
        else:
            source_ref = f"ecourts-order:{data['order_inbox_id']}"
            cur.execute("""
                INSERT INTO case_works (
                    case_record_id,case_number,title,details,assigned_to,due_date,
                    priority,status,source,created_by,external_source_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING','ECOURTS_AI_ORDER',%s,%s)
                ON CONFLICT (source,external_source_id)
                    WHERE external_source_id IS NOT NULL
                DO UPDATE SET
                    title=EXCLUDED.title,details=EXCLUDED.details,
                    assigned_to=EXCLUDED.assigned_to,due_date=EXCLUDED.due_date,
                    priority=EXCLUDED.priority,updated_at=NOW()
                RETURNING id
            """, (
                int(data["local_case_pk"]), data["case_number"], data["title"],
                data.get("details"), data["assigned_to"], data.get("due_date"),
                data["priority"], int(actor_id), source_ref,
            ))
            work_id = int(cur.fetchone()["id"])
            cur.execute("""
                UPDATE ecourts_ai_work_proposals
                SET proposal_status='APPROVED',case_work_id=%s,
                    reviewed_by=%s,reviewed_at=NOW()
                WHERE id=%s RETURNING *
            """, (work_id, int(actor_id), int(proposal_id)))
            result = dict(cur.fetchone())
            cur.execute("""
                SELECT telegram_user_id FROM staff_accounts
                WHERE is_active=TRUE
                  AND LOWER(TRIM(staff_name))=LOWER(TRIM(%s))
                ORDER BY created_at DESC LIMIT 1
            """, (data["assigned_to"],))
            staff_row = cur.fetchone()
            result["telegram_user_id"] = (
                int(staff_row["telegram_user_id"]) if staff_row else None
            )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
