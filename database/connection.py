"""PostgreSQL connection helpers.

Phase 1 intentionally preserves the existing bot's shared-connection behaviour.
Later phases will migrate command modules to short-lived pooled transactions.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import psycopg2
from psycopg2.extensions import connection as PgConnection

from config import DATABASE_URL

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_connection: Optional[PgConnection] = None


def get_connection() -> PgConnection:
    """Return a live PostgreSQL connection, reconnecting when required."""
    global _connection

    with _lock:
        if _connection is None or _connection.closed:
            logger.info("Opening PostgreSQL connection")
            _connection = psycopg2.connect(
                DATABASE_URL,
                connect_timeout=15,
                application_name="law-office-bot-v2",
            )

        return _connection


def close_connection() -> None:
    """Close the shared compatibility connection."""
    global _connection

    with _lock:
        if _connection is not None and not _connection.closed:
            _connection.close()
        _connection = None
