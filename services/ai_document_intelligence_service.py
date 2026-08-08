"""Verified document-context builder for Ajay AI.

Reads only locally indexed case_files metadata/content fields that actually exist.
It is deliberately fail-safe: missing optional columns simply reduce context.
"""
from __future__ import annotations

from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL
from services.case_workspace_service import CaseSummary
from services.case_document_service import case_identifiers


def _connect():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-bot-ajay-ai-doc-intelligence",
    )


def _columns(cur, table: str) -> set[str]:
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name=%s""",
        (table,),
    )
    return {str(r[0]) for r in cur.fetchall()}


def build_document_context(case: CaseSummary, limit: int = 30) -> tuple[str, list[str]]:
    """Return grounded indexed-document context plus unavailable-source notes."""
    identifiers = case_identifiers(case)
    unavailable: list[str] = []
    with _connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cols = _columns(cur, "case_files")
        if not cols:
            return "No indexed case document table is available.", ["documents"]

        optional_text = next(
            (c for c in ("extracted_text", "document_text", "ocr_text", "text_content", "content")
             if c in cols),
            None,
        )
        select = [
            "id",
            "file_name",
            "COALESCE(category, 'MISCELLANEOUS') AS category",
            "drive_file_link",
            "uploaded_at",
        ]
        if optional_text:
            select.append(f"{optional_text} AS indexed_text")
        else:
            select.append("NULL::text AS indexed_text")
            unavailable.append("document full text / OCR")

        sql = f"""
            SELECT {", ".join(select)}
            FROM case_files
            WHERE LOWER(TRIM(case_id)) = ANY(
                SELECT LOWER(TRIM(value))
                FROM UNNEST(%s::text[]) AS value
            )
            ORDER BY uploaded_at DESC NULLS LAST, id DESC
            LIMIT %s
        """
        cur.execute(sql, (identifiers, limit))
        rows = cur.fetchall()

    if not rows:
        return "No indexed documents were found for this case.", ["documents"]

    lines = [
        "VERIFIED INDEXED CASE DOCUMENTS",
        "Use only the information below as document-derived material.",
        f"Indexed documents found: {len(rows)}",
        "",
    ]
    for i, row in enumerate(rows, 1):
        lines += [
            f"DOCUMENT {i}",
            f"File ID: {row.get('id')}",
            f"Name: {row.get('file_name') or 'Unnamed document'}",
            f"Category: {row.get('category') or 'Miscellaneous'}",
            f"Drive link: {row.get('drive_file_link') or 'Not indexed'}",
            f"Uploaded: {row.get('uploaded_at') or 'Not recorded'}",
        ]
        text = str(row.get("indexed_text") or "").strip()
        if text:
            # Bound prompt size while preserving a useful verified excerpt.
            lines.append("Indexed text excerpt:")
            lines.append(text[:8000])
        else:
            lines.append("Indexed text excerpt: Not available.")
        lines.append("")

    return "\n".join(lines).strip(), unavailable
