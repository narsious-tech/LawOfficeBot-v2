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
