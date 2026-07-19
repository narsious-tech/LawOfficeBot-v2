"""Read-only case document queries for LawOfficeBot v3 Sprint 5.

The existing commands.files upload workflow remains authoritative for uploads.
This service only reads locally indexed Google Drive document metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.case_workspace_service import CaseSummary


CATEGORY_NAMES = {
    "PLEADINGS": "Pleadings",
    "ORDERS": "Orders",
    "EVIDENCE": "Evidence",
    "JUDGMENTS": "Judgments",
    "CORRESPONDENCE": "Correspondence",
    "MISCELLANEOUS": "Miscellaneous",
}


@dataclass(frozen=True)
class CaseDocument:
    file_id: int
    file_name: str
    category: str
    drive_link: str
    uploaded_at: Any
    uploaded_by: str
    version_number: int | None = None


def _connect():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-bot-v3-case-documents",
    )


def case_identifiers(case: CaseSummary) -> list[str]:
    values = []
    for value in (case.case_number, case.case_id, case.ad_case_id):
        value = (value or "").strip()
        if value and value != "-" and value not in values:
            values.append(value)
    return values or ["__no_case_identifier__"]


def _category(value: Any) -> str:
    key = str(value or "MISCELLANEOUS").strip().upper()
    return CATEGORY_NAMES.get(key, key.title() or "Miscellaneous")


def _format_datetime(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y · %I:%M %p")
    return str(value)


def list_case_documents(
    case: CaseSummary,
    *,
    category: str | None = None,
    limit: int = 20,
) -> list[CaseDocument]:
    identifiers = case_identifiers(case)
    params: list[Any] = [identifiers]
    category_clause = ""
    if category:
        category_clause = "AND UPPER(COALESCE(cf.category, 'MISCELLANEOUS')) = %s"
        params.append(category.upper())
    params.append(limit)

    sql = f"""
        SELECT
            cf.id,
            cf.file_name,
            COALESCE(cf.category, 'MISCELLANEOUS') AS category,
            cf.drive_file_link,
            cf.uploaded_at,
            COALESCE(sa.staff_name, 'Admin / Unlinked User') AS uploaded_by,
            NULL::integer AS version_number
        FROM case_files cf
        LEFT JOIN staff_accounts sa
          ON sa.telegram_user_id = cf.uploaded_by
        WHERE LOWER(TRIM(cf.case_id)) = ANY(
            SELECT LOWER(TRIM(value))
            FROM UNNEST(%s::text[]) AS value
        )
        {category_clause}
        ORDER BY cf.uploaded_at DESC NULLS LAST, cf.id DESC
        LIMIT %s
    """

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            return [
                CaseDocument(
                    file_id=int(row["id"]),
                    file_name=str(row.get("file_name") or "Unnamed document"),
                    category=_category(row.get("category")),
                    drive_link=str(row.get("drive_file_link") or ""),
                    uploaded_at=row.get("uploaded_at"),
                    uploaded_by=str(row.get("uploaded_by") or "Admin / Unlinked User"),
                    version_number=row.get("version_number"),
                )
                for row in rows
            ]
    finally:
        conn.close()


def document_counts(case: CaseSummary) -> dict[str, int]:
    identifiers = case_identifiers(case)
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    UPPER(COALESCE(category, 'MISCELLANEOUS')) AS category,
                    COUNT(*)::integer AS total
                FROM case_files
                WHERE LOWER(TRIM(case_id)) = ANY(
                    SELECT LOWER(TRIM(value))
                    FROM UNNEST(%s::text[]) AS value
                )
                GROUP BY UPPER(COALESCE(category, 'MISCELLANEOUS'))
                """,
                (identifiers,),
            )
            result = {key: 0 for key in CATEGORY_NAMES}
            for row in cur.fetchall():
                result[str(row["category"])] = int(row["total"])
            result["TOTAL"] = sum(result.values())
            return result
    finally:
        conn.close()


def render_document_list(case: CaseSummary, documents: list[CaseDocument], heading: str) -> str:
    identifier = case.case_number if case.case_number != "-" else case.case_id
    lines = [
        f"📂 <b>{heading}</b>",
        f"🆔 <b>{identifier}</b>",
        f"📌 {case.case_title}",
        "",
    ]
    if not documents:
        lines.append("No indexed documents found in this category.")
        return "\n".join(lines)

    for doc in documents:
        lines.extend([
            f"🆔 <b>File #{doc.file_id}</b> · {doc.category}",
            f"📄 {doc.file_name}",
            f"👤 {doc.uploaded_by}",
            f"🕒 {_format_datetime(doc.uploaded_at)}",
            f"/openfile {doc.file_id}",
            "──────────",
        ])
    return "\n".join(lines)
