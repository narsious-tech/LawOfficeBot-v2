"""Database-backed search for indexed case documents."""

from typing import List, Dict


def search_documents(connection, query: str, limit: int = 30) -> List[Dict]:
    term = f"%{(query or '').strip()}%"
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, case_id, file_name, category, drive_file_link, uploaded_at
            FROM case_files
            WHERE file_name ILIKE %s OR case_id ILIKE %s OR COALESCE(category, '') ILIKE %s
            ORDER BY uploaded_at DESC NULLS LAST, id DESC
            LIMIT %s
            """,
            (term, term, term, limit),
        )
        rows = cur.fetchall()
    return [
        {"id": r[0], "case_id": r[1], "file_name": r[2], "category": r[3], "link": r[4], "uploaded_at": r[5]}
        for r in rows
    ]
