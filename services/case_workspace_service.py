"""Read-only case workspace queries for LawOfficeBot v3 Sprint 3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL


@dataclass(frozen=True)
class CaseSummary:
    db_id: int
    case_id: str
    case_number: str
    case_title: str
    client_name: str
    mobile: str
    court_name: str
    judge_name: str
    opposite_party: str
    next_hearing: str
    status: str
    drive_folder_link: str
    fee_agreed: str
    advance_received: str
    notes: str
    ad_case_id: str
    ad_sync_status: str


def _text(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _connect():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-bot-v3-case-workspace",
    )


def _row_to_case(row: dict[str, Any]) -> CaseSummary:
    return CaseSummary(
        db_id=int(row["id"]),
        case_id=_text(row.get("case_id")),
        case_number=_text(row.get("case_number")),
        case_title=_text(row.get("case_title")),
        client_name=_text(row.get("client_name")),
        mobile=_text(row.get("mobile")),
        court_name=_text(row.get("court_name")),
        judge_name=_text(row.get("judge_name")),
        opposite_party=_text(row.get("opposite_party")),
        next_hearing=_text(row.get("next_hearing") or row.get("hearing_date")),
        status=_text(row.get("status"), "OPEN"),
        drive_folder_link=_text(row.get("drive_folder_link")),
        fee_agreed=_text(row.get("fee_agreed")),
        advance_received=_text(row.get("advance_received")),
        notes=_text(row.get("notes")),
        ad_case_id=_text(row.get("ad_case_id")),
        ad_sync_status=_text(row.get("ad_sync_status")),
    )


def search_cases(query: str, limit: int = 10) -> list[CaseSummary]:
    term = f"%{query.strip()}%"
    sql = """
        SELECT *
        FROM cases
        WHERE
            COALESCE(case_id, '') ILIKE %s OR
            COALESCE(case_number, '') ILIKE %s OR
            COALESCE(case_title, '') ILIKE %s OR
            COALESCE(client_name, '') ILIKE %s OR
            COALESCE(mobile, '') ILIKE %s OR
            COALESCE(opposite_party, '') ILIKE %s OR
            COALESCE(ad_case_id, '') ILIKE %s
        ORDER BY
            CASE WHEN COALESCE(status, 'OPEN') = 'OPEN' THEN 0 ELSE 1 END,
            id DESC
        LIMIT %s
    """
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (term, term, term, term, term, term, term, limit))
            return [_row_to_case(dict(row)) for row in cur.fetchall()]
    finally:
        conn.close()


def recent_cases(limit: int = 10) -> list[CaseSummary]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM cases
                ORDER BY
                    CASE WHEN COALESCE(status, 'OPEN') = 'OPEN' THEN 0 ELSE 1 END,
                    id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [_row_to_case(dict(row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_case(db_id: int) -> Optional[CaseSummary]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cases WHERE id = %s LIMIT 1", (db_id,))
            row = cur.fetchone()
            return _row_to_case(dict(row)) if row else None
    finally:
        conn.close()


def get_case_counts(case: CaseSummary) -> dict[str, int]:
    """Return safe local counts without writing to Advocate Diaries."""
    identifiers = [
        value for value in (case.case_number, case.case_id, case.ad_case_id)
        if value and value != "-"
    ]
    if not identifiers:
        identifiers = ["__no_case_identifier__"]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE COALESCE(status, 'PENDING') <> 'COMPLETED'
                  AND case_number = ANY(%s)
                """,
                (identifiers,),
            )
            pending_tasks = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE COALESCE(status, 'PENDING') = 'COMPLETED'
                  AND case_number = ANY(%s)
                """,
                (identifiers,),
            )
            completed_tasks = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(*)
                FROM fee_installments
                WHERE case_number = ANY(%s)
                """,
                (identifiers,),
            )
            installments = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(*)
                FROM case_responsibility
                WHERE case_number = ANY(%s)
                """,
                (identifiers,),
            )
            staff = int(cur.fetchone()[0])

        return {
            "pending_tasks": pending_tasks,
            "completed_tasks": completed_tasks,
            "installments": installments,
            "staff": staff,
        }
    finally:
        conn.close()


def get_case_tasks(case: CaseSummary, limit: int = 15) -> list[dict[str, Any]]:
    identifiers = [
        value for value in (case.case_number, case.case_id, case.ad_case_id)
        if value and value != "-"
    ] or ["__no_case_identifier__"]
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, assigned_to, task, deadline, due_at, status, priority,
                       source_type, source_work_id
                FROM tasks
                WHERE case_number = ANY(%s)
                ORDER BY
                    CASE WHEN COALESCE(status, 'PENDING') = 'COMPLETED' THEN 1 ELSE 0 END,
                    COALESCE(due_at, CURRENT_TIMESTAMP + INTERVAL '100 years'),
                    id DESC
                LIMIT %s
                """,
                (identifiers, limit),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_case_staff(case: CaseSummary) -> list[dict[str, Any]]:
    identifiers = [
        value for value in (case.case_number, case.case_id, case.ad_case_id)
        if value and value != "-"
    ] or ["__no_case_identifier__"]
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT staff_name, responsibility
                FROM case_responsibility
                WHERE case_number = ANY(%s)
                ORDER BY id
                """,
                (identifiers,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_fee_installments(case: CaseSummary) -> list[dict[str, Any]]:
    identifiers = [
        value for value in (case.case_number, case.case_id, case.ad_case_id)
        if value and value != "-"
    ] or ["__no_case_identifier__"]
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT amount, date
                FROM fee_installments
                WHERE case_number = ANY(%s)
                ORDER BY id DESC
                LIMIT 20
                """,
                (identifiers,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
