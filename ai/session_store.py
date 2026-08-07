from __future__ import annotations

from typing import Any
import psycopg2
from config import DATABASE_URL


class AISessionStore:
    def _connect(self):
        return psycopg2.connect(DATABASE_URL, connect_timeout=15, application_name="law-office-ai")

    def create_session(self, user_id: int, feature: str = "general", case_reference: str | None = None) -> int:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO ai_sessions (telegram_user_id, feature, case_reference)
                           VALUES (%s,%s,%s) RETURNING id""",
                        (user_id, feature, case_reference),
                    )
                    return int(cur.fetchone()[0])
        finally:
            conn.close()

    def add_message(self, session_id: int, role: str, content: str) -> None:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_messages (session_id, role, content) VALUES (%s,%s,%s)",
                        (session_id, role, content),
                    )
                    cur.execute("UPDATE ai_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=%s", (session_id,))
        finally:
            conn.close()

    def recent_messages(self, session_id: int, limit: int = 10) -> list[dict[str, str]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT role, content FROM ai_messages WHERE session_id=%s
                       ORDER BY id DESC LIMIT %s""",
                    (session_id, max(1, min(limit, 30))),
                )
                rows = list(reversed(cur.fetchall()))
                return [{"role": role, "content": content} for role, content in rows]
        finally:
            conn.close()

    def log_usage(self, *, session_id: int | None, user_id: int, feature: str, model: str,
                  input_tokens: int | None, output_tokens: int | None, total_tokens: int | None,
                  duration_ms: int, status: str, error_type: str | None = None) -> None:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO ai_usage
                           (session_id, telegram_user_id, feature, model, input_tokens, output_tokens,
                            total_tokens, duration_ms, status, error_type)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (session_id, user_id, feature, model, input_tokens, output_tokens,
                         total_tokens, duration_ms, status, error_type),
                    )
        finally:
            conn.close()

    def usage_summary(self, days: int = 30) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT model,
                              COUNT(*) FILTER (WHERE status='SUCCESS') AS successful_calls,
                              COUNT(*) FILTER (WHERE status='FAILED') AS failed_calls,
                              COALESCE(SUM(input_tokens) FILTER (WHERE status='SUCCESS'),0),
                              COALESCE(SUM(output_tokens) FILTER (WHERE status='SUCCESS'),0),
                              COALESCE(SUM(total_tokens) FILTER (WHERE status='SUCCESS'),0)
                       FROM ai_usage
                       WHERE created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                       GROUP BY model ORDER BY model""",
                    (max(1, min(int(days), 366)),),
                )
                return [
                    {
                        "model": row[0],
                        "successful_calls": int(row[1] or 0),
                        "failed_calls": int(row[2] or 0),
                        "input_tokens": int(row[3] or 0),
                        "output_tokens": int(row[4] or 0),
                        "total_tokens": int(row[5] or 0),
                    }
                    for row in cur.fetchall()
                ]
        finally:
            conn.close()
