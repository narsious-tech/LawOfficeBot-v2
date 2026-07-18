from datetime import datetime
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

from config import DATABASE_URL


EVENT_DEFINITIONS = {
    "DOCUMENT_UPLOADED": {
        "category": "documents",
        "type": "DOCUMENT",
        "title": "Document uploaded",
        "status": "UPLOADED",
    },
    "DOCUMENT_DUPLICATE_UPLOADED": {
        "category": "documents",
        "type": "DOCUMENT",
        "title": "Duplicate document uploaded",
        "status": "UPLOADED",
    },
    "DRIVE_FOLDER_CREATED": {
        "category": "documents",
        "type": "DRIVE",
        "title": "Google Drive folder created",
        "status": "READY",
    },
    "TASK_ASSIGNED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Task assigned",
        "status": "PENDING",
    },
    "TASK_REASSIGNED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Task reassigned",
        "status": "PENDING",
    },
    "TASK_COMPLETED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Task completed",
        "status": "COMPLETED",
    },
    "TASK_REOPENED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Task reopened",
        "status": "PENDING",
    },
    "TASK_PRIORITY_CHANGED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Task priority changed",
        "status": "UPDATED",
    },
    "AD_WORK_ASSIGNED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Advocate Diaries work assigned",
        "status": "PENDING",
    },
    "AD_WORK_COMPLETED": {
        "category": "tasks",
        "type": "TASK",
        "title": "Advocate Diaries work completed",
        "status": "COMPLETED",
    },
}


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def ensure_activity_schema():
    """
    Ensure the Case Activity Center has all columns used by the logger.
    Safe to call repeatedly during startup.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_timeline (
                id SERIAL PRIMARY KEY,
                client_id INTEGER,
                ad_client_id TEXT,
                case_id TEXT,
                case_number TEXT,
                event_type TEXT NOT NULL,
                event_title TEXT NOT NULL,
                event_details TEXT,
                event_status TEXT,
                event_category TEXT,
                source_type TEXT DEFAULT 'SYSTEM',
                source_id TEXT,
                created_by BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_internal BOOLEAN DEFAULT TRUE,
                metadata_json JSONB DEFAULT '{}'::jsonb
            )
        """)

        cur.execute("""
            ALTER TABLE client_timeline
            ADD COLUMN IF NOT EXISTS event_status TEXT
        """)

        cur.execute("""
            ALTER TABLE client_timeline
            ADD COLUMN IF NOT EXISTS event_category TEXT
        """)

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            client_timeline_source_unique_idx
            ON client_timeline (
                source_type,
                source_id,
                event_type
            )
            WHERE source_id IS NOT NULL
        """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def find_case_for_activity(
    cur,
    case_value: str
) -> Optional[Dict[str, Any]]:
    value = (case_value or "").strip()

    if not value:
        return None

    cur.execute("""
        SELECT
            id,
            case_id,
            case_number,
            client_id,
            ad_client_id,
            client_name,
            case_title
        FROM cases
        WHERE
            LOWER(TRIM(COALESCE(case_id, '')))
                = LOWER(TRIM(%s))
            OR
            LOWER(TRIM(COALESCE(case_number, '')))
                = LOWER(TRIM(%s))
        ORDER BY id DESC
        LIMIT 1
    """, (
        value,
        value
    ))

    row = cur.fetchone()

    if not row:
        return None

    return {
        "db_id": row[0],
        "case_id": row[1],
        "case_number": row[2],
        "client_id": row[3],
        "ad_client_id": row[4],
        "client_name": row[5],
        "case_title": row[6],
        "canonical_case_id": (
            row[2]
            or row[1]
            or value
        ),
    }


def log_activity_with_cursor(
    cur,
    *,
    case_value: str,
    event_code: str,
    details: str = "",
    source_module: str = "SYSTEM",
    source_id: Optional[str] = None,
    user_id: Optional[int] = None,
    event_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    is_internal: bool = True
) -> Optional[int]:
    """
    Write an activity using an existing database transaction.

    The caller controls commit/rollback. When the case cannot be found,
    this function returns None instead of interrupting the original action.
    """
    case = find_case_for_activity(
        cur,
        case_value
    )

    if not case:
        return None

    definition = EVENT_DEFINITIONS.get(
        event_code,
        {
            "category": category or "other",
            "type": "ACTIVITY",
            "title": title or event_code.replace("_", " ").title(),
            "status": status or "RECORDED",
        }
    )

    event_type = definition["type"]
    event_title = title or definition["title"]
    event_status = status or definition["status"]
    event_category = category or definition["category"]

    cur.execute("""
        INSERT INTO client_timeline
        (
            client_id,
            ad_client_id,
            case_id,
            case_number,

            event_type,
            event_title,
            event_details,
            event_status,
            event_category,

            source_type,
            source_id,

            created_by,
            event_at,
            is_internal,
            metadata_json
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s,
            %s,
            COALESCE(%s, CURRENT_TIMESTAMP),
            %s,
            %s
        )
        ON CONFLICT (
            source_type,
            source_id,
            event_type
        )
        WHERE source_id IS NOT NULL
        DO UPDATE SET
            event_title = EXCLUDED.event_title,
            event_details = EXCLUDED.event_details,
            event_status = EXCLUDED.event_status,
            event_category = EXCLUDED.event_category,
            event_at = EXCLUDED.event_at,
            metadata_json = EXCLUDED.metadata_json
        RETURNING id
    """, (
        case.get("client_id"),
        case.get("ad_client_id"),
        case.get("case_id"),
        case.get("canonical_case_id"),

        event_type,
        event_title,
        details,
        event_status,
        event_category,

        source_module,
        (
            str(source_id)
            if source_id is not None
            else None
        ),

        user_id,
        event_at,
        is_internal,
        psycopg2.extras.Json(
            metadata or {}
        )
    ))

    row = cur.fetchone()

    return (
        int(row[0])
        if row
        else None
    )


def log_activity(
    *,
    case_value: str,
    event_code: str,
    details: str = "",
    source_module: str = "SYSTEM",
    source_id: Optional[str] = None,
    user_id: Optional[int] = None,
    event_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    is_internal: bool = True,
    suppress_errors: bool = True
) -> Optional[int]:
    """
    Standalone activity logger.

    By default logging errors do not break the original office operation.
    Set suppress_errors=False when the activity itself is the main operation.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        event_id = log_activity_with_cursor(
            cur,
            case_value=case_value,
            event_code=event_code,
            details=details,
            source_module=source_module,
            source_id=source_id,
            user_id=user_id,
            event_at=event_at,
            metadata=metadata,
            title=title,
            status=status,
            category=category,
            is_internal=is_internal
        )

        conn.commit()
        return event_id

    except Exception as exc:
        conn.rollback()

        if suppress_errors:
            print(
                "ACTIVITY LOGGING FAILED: "
                f"{event_code} | "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        raise

    finally:
        cur.close()
        conn.close()
