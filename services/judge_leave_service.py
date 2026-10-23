"""Confirmed judge-leave intake, case exclusion and holiday safeguards."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from copy import deepcopy
from datetime import date
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from pypdf import PdfReader

from config import DATABASE_URL


NAME_STOPWORDS = {
    "hon", "honble", "honourable", "mr", "mrs", "ms", "sh", "shri",
    "smt", "dr", "justice", "judge", "ld", "learned", "district",
    "additional", "sessions", "session", "civil", "judicial", "magistrate",
    "chief", "court", "presiding", "officer", "the",
}


def normalize(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())
    return " ".join(text.split())


def judge_signature(value: Any) -> str:
    tokens = [token for token in normalize(value).split() if token not in NAME_STOPWORDS]
    return " ".join(tokens)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a readable PDF.")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalize(text)) < 20:
        raise ValueError(
            "No searchable text was found. Please upload the original/searchable PDF, "
            "not a scanned photograph."
        )
    return text


def _date_forms(target: date) -> set[str]:
    return {
        target.strftime("%d %m %Y"), target.strftime("%d %m %y"),
        target.strftime("%Y %m %d"),
        normalize(target.strftime("%d %B %Y")),
        normalize(target.strftime("%d %b %Y")),
    }


def pdf_mentions_date(text: str, target: date) -> bool:
    normalized = normalize(text)
    return any(form in normalized for form in _date_forms(target))


def match_leave_groups(pdf_text: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match only cause-list judge names appearing in the leave PDF.

    Matching against tomorrow's known judge list is deliberately safer than trying
    to invent names from arbitrary PDF text. Confirmation remains mandatory.
    """
    haystack = f" {normalize(pdf_text)} "
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups or []:
        judge = str(group.get("judge_name") or "").strip()
        signature = judge_signature(judge)
        tokens = signature.split()
        if len(tokens) < 2 or f" {signature} " not in haystack:
            continue
        court = str(group.get("court_name") or "").strip()
        key = (normalize(judge), normalize(court))
        if key in seen:
            continue
        seen.add(key)
        cases = [
            {
                "case_number": str(item.get("case_number") or "").strip(),
                "case_title": str(item.get("case_title") or "").strip(),
            }
            for item in group.get("cases") or []
        ]
        matches.append({
            "judge_name": judge,
            "judge_key": normalize(judge),
            "court_name": court,
            "cases": cases,
            "case_count": len(cases),
        })
    return matches


def ensure_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS judge_leave_batches (
            id BIGSERIAL PRIMARY KEY,
            target_date DATE NOT NULL,
            source_channel TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            uploaded_by_telegram BIGINT,
            uploaded_by_name TEXT NOT NULL,
            filename TEXT,
            document_sha256 TEXT NOT NULL,
            date_mentioned BOOLEAN NOT NULL DEFAULT FALSE,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','CONFIRMED','CANCELLED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            confirmed_at TIMESTAMPTZ,
            UNIQUE(target_date,document_sha256)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS judge_leave_entries (
            id BIGSERIAL PRIMARY KEY,
            batch_id BIGINT NOT NULL REFERENCES judge_leave_batches(id) ON DELETE CASCADE,
            target_date DATE NOT NULL,
            judge_name TEXT NOT NULL,
            judge_key TEXT NOT NULL,
            court_name TEXT,
            case_count INTEGER NOT NULL DEFAULT 0,
            cases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            UNIQUE(batch_id,judge_key,court_name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS office_calendar_notifications (
            id BIGSERIAL PRIMARY KEY,
            target_date DATE NOT NULL,
            notification_type TEXT NOT NULL,
            details TEXT,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(target_date,notification_type)
        )
    """)


def create_leave_batch(
    *, target_date: date, pdf_bytes: bytes, filename: str, pdf_text: str,
    matches: list[dict[str, Any]], source_channel: str, source_identity: str,
    actor_name: str, actor_telegram_id: int | None,
) -> dict[str, Any]:
    if not matches:
        raise ValueError("No next-court-day judge could be matched safely in this PDF.")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ensure_schema(cur)
            cur.execute("""
                INSERT INTO judge_leave_batches(
                    target_date,source_channel,source_identity,uploaded_by_telegram,
                    uploaded_by_name,filename,document_sha256,date_mentioned
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(target_date,document_sha256) DO UPDATE SET
                    source_identity=EXCLUDED.source_identity,
                    uploaded_by_telegram=EXCLUDED.uploaded_by_telegram,
                    uploaded_by_name=EXCLUDED.uploaded_by_name,
                    filename=EXCLUDED.filename
                RETURNING *
            """, (
                target_date, source_channel, source_identity, actor_telegram_id,
                actor_name, filename, digest, pdf_mentions_date(pdf_text, target_date),
            ))
            batch = dict(cur.fetchone())
            if batch["status"] == "CONFIRMED":
                raise ValueError("This leave PDF was already confirmed for the selected date.")
            cur.execute("DELETE FROM judge_leave_entries WHERE batch_id=%s", (batch["id"],))
            for match in matches:
                cur.execute("""
                    INSERT INTO judge_leave_entries(
                        batch_id,target_date,judge_name,judge_key,court_name,case_count,cases_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                """, (
                    batch["id"], target_date, match["judge_name"], match["judge_key"],
                    match["court_name"], match["case_count"], json.dumps(match["cases"]),
                ))
        conn.commit()
        return {**batch, "matches": matches}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_batch(batch_id: int) -> dict[str, Any] | None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ensure_schema(cur)
            cur.execute("SELECT * FROM judge_leave_batches WHERE id=%s", (int(batch_id),))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute("SELECT * FROM judge_leave_entries WHERE batch_id=%s ORDER BY id", (int(batch_id),))
            return {**dict(batch), "entries": [dict(row) for row in cur.fetchall()]}
    finally:
        conn.close()


def decide_batch(batch_id: int, source_identity: str, confirm: bool) -> dict[str, Any]:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ensure_schema(cur)
            cur.execute("SELECT * FROM judge_leave_batches WHERE id=%s FOR UPDATE", (int(batch_id),))
            batch = cur.fetchone()
            if not batch:
                raise ValueError("Leave preview not found.")
            if str(batch["source_identity"]) != str(source_identity):
                raise PermissionError("Only the person who uploaded this PDF may confirm it.")
            if batch["status"] != "PENDING":
                raise ValueError(f"This leave preview is already {str(batch['status']).lower()}.")
            status = "CONFIRMED" if confirm else "CANCELLED"
            cur.execute("""
                UPDATE judge_leave_batches
                SET status=%s,confirmed_at=CASE WHEN %s='CONFIRMED' THEN NOW() ELSE NULL END
                WHERE id=%s RETURNING *
            """, (status, status, int(batch_id)))
            result = dict(cur.fetchone())
            cur.execute("SELECT * FROM judge_leave_entries WHERE batch_id=%s ORDER BY id", (int(batch_id),))
            result["entries"] = [dict(row) for row in cur.fetchall()]
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirmed_leave_entries(target_date: date, *, read_only: bool = False) -> list[dict[str, Any]]:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        if read_only:
            conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not read_only:
                ensure_schema(cur)
            cur.execute("""
                SELECT DISTINCT ON (e.judge_key,e.court_name) e.*
                FROM judge_leave_entries e
                JOIN judge_leave_batches b ON b.id=e.batch_id
                WHERE e.target_date=%s AND b.status='CONFIRMED'
                ORDER BY e.judge_key,e.court_name,e.id DESC
            """, (target_date,))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def apply_leave_exclusions(
    groups: list[dict[str, Any]], target_date: date,
    entries: list[dict[str, Any]] | None = None,
    *, read_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries = confirmed_leave_entries(target_date, read_only=read_only) if entries is None else entries
    keys = {str(entry.get("judge_key") or "") for entry in entries}
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for group in deepcopy(groups or []):
        if normalize(group.get("judge_name")) in keys:
            excluded.extend({
                **dict(case),
                "judge_name": group.get("judge_name"),
                "court_name": group.get("court_name"),
            } for case in group.get("cases") or [])
        else:
            kept.append(group)
    return kept, excluded, entries


def leave_alert_text(target_date: date) -> str | None:
    entries = confirmed_leave_entries(target_date)
    if not entries:
        return None
    lines = [
        "👨‍⚖️ JUDGE LEAVE ALERT",
        f"📅 Court date: {target_date:%d-%m-%Y}",
        "",
    ]
    total = 0
    for entry in entries:
        count = int(entry.get("case_count") or 0)
        total += count
        lines.append(f"• {entry.get('judge_name') or '-'} · {entry.get('court_name') or 'Court'} · {count} matter(s)")
    lines.extend([
        "",
        f"📁 {total} listed matter(s) will be excluded from today's physical-file selection.",
        "The case dates and Advocate Diaries records are not changed.",
    ])
    return "\n".join(lines)


def holiday_name(target_date: date) -> str | None:
    overrides = os.getenv("COURT_HOLIDAY_OVERRIDES", "").strip()
    if overrides:
        try:
            value = json.loads(overrides).get(target_date.isoformat())
            if value:
                return str(value).strip()
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        import holidays

        calendar = holidays.country_holidays(
            "IN",
            subdiv=os.getenv("COURT_HOLIDAY_SUBDIV", "PB").strip() or "PB",
            years=[target_date.year],
            language="en_IN",
        )
        return str(calendar.get(target_date) or "").strip() or None
    except Exception:
        return None


def holiday_greeting(target_date: date, active_case_count: int) -> str | None:
    name = holiday_name(target_date)
    if not name or int(active_case_count) > 2:
        return None
    case_line = (
        "No court matters are listed."
        if active_case_count == 0
        else f"Only {active_case_count} court matter(s) are listed."
    )
    return (
        f"🎉 {name.upper()}\n\n"
        f"Warm wishes to the entire Law Office team for {name}.\n"
        f"📅 {target_date:%d-%m-%Y}\n"
        f"⚖️ {case_line}\n\n"
        "Please still follow any case-specific duty already assigned to you."
    )


def notification_was_sent(target_date: date, kind: str) -> bool:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            ensure_schema(cur)
            cur.execute(
                "SELECT 1 FROM office_calendar_notifications WHERE target_date=%s AND notification_type=%s",
                (target_date, kind),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def mark_notification_sent(target_date: date, kind: str, details: str) -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            ensure_schema(cur)
            cur.execute("""
                INSERT INTO office_calendar_notifications(target_date,notification_type,details)
                VALUES (%s,%s,%s) ON CONFLICT(target_date,notification_type) DO NOTHING
            """, (target_date, kind, details[:4000]))
        conn.commit()
    finally:
        conn.close()
