from __future__ import annotations

import psycopg2
from config import DATABASE_URL


class OfficeKnowledgeService:
    """Read-only, bounded office context for AI. No raw credentials or unrestricted SQL."""

    def case_snapshot(self, case_reference: str) -> str:
        ref = case_reference.strip()
        if not ref:
            return "No case reference supplied."
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-knowledge")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT case_id, client_name, case_type, court_name, opposite_party,
                              hearing_date, status, notes
                       FROM cases
                       WHERE case_id ILIKE %s OR client_name ILIKE %s OR opposite_party ILIKE %s
                       ORDER BY id DESC LIMIT 5""",
                    (f"%{ref}%", f"%{ref}%", f"%{ref}%"),
                )
                rows = cur.fetchall()
            if not rows:
                return f"No matching local case found for: {ref}"
            blocks = []
            for row in rows:
                blocks.append(
                    "\n".join([
                        f"Case reference: {row[0] or 'Not recorded'}",
                        f"Client: {row[1] or 'Not recorded'}",
                        f"Case type: {row[2] or 'Not recorded'}",
                        f"Court: {row[3] or 'Not recorded'}",
                        f"Opposite party: {row[4] or 'Not recorded'}",
                        f"Hearing date: {row[5] or 'Not recorded'}",
                        f"Status: {row[6] or 'Not recorded'}",
                        f"Internal notes: {row[7] or 'Not recorded'}",
                    ])
                )
            return "\n\n---\n\n".join(blocks)
        finally:
            conn.close()
