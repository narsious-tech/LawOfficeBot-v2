from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from config import DATABASE_URL


VISIBLE_DEFAULT_STATUSES = {
    "SENT",
    "SENT_MANUALLY",
    "DELIVERED",
    "READ",
    "REPLIED",
    "CONFIRMED",
    "VERIFIED",
    "DETAILS_CONFIRMED",
    "PENDING",
    "OPEN",
    "COMPLETED",
}


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def ensure_client_timeline_table():
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
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                event_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                is_internal BOOLEAN
                    DEFAULT TRUE,

                metadata_json JSONB
                    DEFAULT '{}'::jsonb
            )
        """)

        cur.execute("""
            ALTER TABLE client_timeline
            ADD COLUMN IF NOT EXISTS
                event_status TEXT
        """)

        cur.execute("""
            ALTER TABLE client_timeline
            ADD COLUMN IF NOT EXISTS
                event_category TEXT
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            client_timeline_case_idx
            ON client_timeline (
                LOWER(
                    TRIM(
                        COALESCE(
                            case_number,
                            case_id,
                            ''
                        )
                    )
                )
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            client_timeline_client_idx
            ON client_timeline (client_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            client_timeline_event_type_idx
            ON client_timeline (
                event_type
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            client_timeline_event_category_idx
            ON client_timeline (
                event_category
            )
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


def get_case_record(
    cur,
    case_value: str
) -> Optional[Dict[str, Any]]:
    cur.execute("""
        SELECT
            id,
            case_id,
            case_number,
            case_title,
            client_name,
            mobile,
            client_id,
            ad_client_id,
            status,
            court_name,
            judge_name,
            COALESCE(
                next_hearing,
                hearing_date
            ) AS next_hearing
        FROM cases
        WHERE
            LOWER(
                TRIM(
                    COALESCE(case_id, '')
                )
            ) = LOWER(TRIM(%s))
            OR
            LOWER(
                TRIM(
                    COALESCE(case_number, '')
                )
            ) = LOWER(TRIM(%s))
        ORDER BY id DESC
        LIMIT 1
    """, (
        case_value,
        case_value
    ))

    row = cur.fetchone()

    if not row:
        return None

    keys = [
        "id",
        "case_id",
        "case_number",
        "case_title",
        "client_name",
        "mobile",
        "client_id",
        "ad_client_id",
        "status",
        "court_name",
        "judge_name",
        "next_hearing",
    ]

    data = dict(zip(keys, row))

    data["canonical_case_id"] = (
        data.get("case_number")
        or data.get("case_id")
        or case_value
    )

    return data


def add_timeline_event(
    cur,
    *,
    case: Dict[str, Any],
    event_type: str,
    event_title: str,
    event_details: str = "",
    event_status: str = "",
    event_category: str = "",
    source_type: str = "SYSTEM",
    source_id: Optional[str] = None,
    created_by: Optional[int] = None,
    event_at: Optional[datetime] = None,
    is_internal: bool = True,
    metadata_json: Optional[Dict[str, Any]] = None
) -> Optional[int]:
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
            event_title =
                EXCLUDED.event_title,
            event_details =
                EXCLUDED.event_details,
            event_status =
                EXCLUDED.event_status,
            event_category =
                EXCLUDED.event_category,
            event_at =
                EXCLUDED.event_at,
            metadata_json =
                EXCLUDED.metadata_json
        RETURNING id
    """, (
        case.get("client_id"),
        case.get("ad_client_id"),
        case.get("case_id"),
        case.get("canonical_case_id"),

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
        psycopg2.extras.Json(
            metadata_json or {}
        )
    ))

    row = cur.fetchone()

    return (
        int(row[0])
        if row
        else None
    )


def add_manual_timeline_event(
    *,
    case_value: str,
    event_title: str,
    event_details: str,
    created_by: int
) -> int:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            raise ValueError(
                f"Case not found: {case_value}"
            )

        event_id = add_timeline_event(
            cur,
            case=case,
            event_type="MANUAL_NOTE",
            event_title=event_title,
            event_details=event_details,
            event_status="RECORDED",
            event_category="notes",
            source_type="MANUAL",
            source_id=None,
            created_by=created_by,
            is_internal=True
        )

        conn.commit()

        if not event_id:
            raise RuntimeError(
                "Timeline event could not be created."
            )

        return event_id

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def communication_title(
    message_type: str,
    delivery_status: str
) -> str:
    message_labels = {
        "CLIENT_WELCOME": "Welcome message",
        "NEW_CASE": "New-case message",
        "CASE_STATUS": "Case-status message",
        "HEARING_REMINDER": "Hearing reminder",
        "DOCUMENT_REQUEST": "Document request",
        "FEE_REMINDER": "Fee reminder",
        "CASE_CLOSURE": "Case-closure message",
    }

    status_labels = {
        "DRAFT": "prepared",
        "CANCELLED": "cancelled",
        "SENT_MANUALLY": "sent",
        "SENT": "sent",
        "DELIVERED": "delivered",
        "READ": "read",
        "REPLIED": "replied to",
    }

    message_label = message_labels.get(
        message_type,
        "Client communication"
    )

    status_label = status_labels.get(
        delivery_status,
        delivery_status.lower().replace(
            "_",
            " "
        )
        if delivery_status
        else "updated"
    )

    return (
        f"{message_label} {status_label}"
    )


def log_communication_event(
    cur,
    *,
    case: Dict[str, Any],
    message_id: int,
    message_type: str,
    delivery_status: str,
    communication_ref: str,
    created_by: Optional[int] = None,
    event_at: Optional[datetime] = None
) -> Optional[int]:
    title = communication_title(
        message_type,
        delivery_status
    )

    details = (
        f"Status: {delivery_status or '-'}\n"
        f"Reference: {communication_ref or '-'}"
    )

    return add_timeline_event(
        cur,
        case=case,
        event_type="COMMUNICATION",
        event_title=title,
        event_details=details,
        event_status=delivery_status,
        event_category="communications",
        source_type="CLIENT_MESSAGE",
        source_id=str(message_id),
        created_by=created_by,
        event_at=event_at,
        is_internal=True,
        metadata_json={
            "message_id": message_id,
            "message_type": message_type,
            "delivery_status": delivery_status,
            "communication_ref": communication_ref,
        }
    )


def log_verification_event(
    cur,
    *,
    case: Dict[str, Any],
    status: str,
    note: str = "",
    created_by: Optional[int] = None
) -> Optional[int]:
    status_upper = (
        status
        or ""
    ).upper()

    if status_upper in {
        "CONFIRMED",
        "VERIFIED",
        "DETAILS_CONFIRMED",
    }:
        title = "Client details confirmed"
        event_type = "CLIENT_VERIFIED"

    elif status_upper == "CHANGE_REQUESTED":
        title = "Client requested corrections"
        event_type = (
            "CLIENT_CHANGE_REQUESTED"
        )

    else:
        title = "Client verification updated"
        event_type = (
            "CLIENT_VERIFICATION"
        )

    return add_timeline_event(
        cur,
        case=case,
        event_type=event_type,
        event_title=title,
        event_details=note,
        event_status=status_upper,
        event_category="client_updates",
        source_type="CLIENT_VERIFICATION",
        source_id=None,
        created_by=created_by,
        is_internal=True,
        metadata_json={
            "verification_status": status_upper
        }
    )


def backfill_timeline_for_case(
    case_value: str
) -> Dict[str, int]:
    conn = get_db_connection()
    cur = conn.cursor()

    counts = {
        "messages": 0,
        "tasks": 0,
        "files": 0,
        "hearings": 0,
    }

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            raise ValueError(
                f"Case not found: {case_value}"
            )

        canonical = case[
            "canonical_case_id"
        ]

        cur.execute("""
            SELECT
                id,
                message_type,
                delivery_status,
                communication_ref,
                created_at,
                sent_at
            FROM client_messages
            WHERE
                LOWER(
                    TRIM(
                        COALESCE(
                            related_case_id,
                            case_id,
                            ''
                        )
                    )
                )
                =
                LOWER(TRIM(%s))
            ORDER BY id ASC
        """, (
            canonical,
        ))

        for (
            message_id,
            message_type,
            delivery_status,
            communication_ref,
            created_at,
            sent_at
        ) in cur.fetchall():
            event_id = (
                log_communication_event(
                    cur,
                    case=case,
                    message_id=message_id,
                    message_type=(
                        message_type
                        or "CLIENT_MESSAGE"
                    ),
                    delivery_status=(
                        delivery_status
                        or "DRAFT"
                    ),
                    communication_ref=(
                        communication_ref
                        or ""
                    ),
                    event_at=(
                        sent_at
                        or created_at
                    )
                )
            )

            if event_id:
                counts["messages"] += 1

        cur.execute("""
            SELECT
                id,
                task,
                status,
                assigned_to,
                created_at,
                completed_at
            FROM tasks
            WHERE
                LOWER(
                    TRIM(
                        COALESCE(
                            case_number,
                            ''
                        )
                    )
                )
                =
                LOWER(TRIM(%s))
            ORDER BY id ASC
        """, (
            canonical,
        ))

        for (
            task_id,
            task_text,
            task_status,
            assigned_to,
            created_at,
            completed_at
        ) in cur.fetchall():
            status_upper = str(
                task_status or ""
            ).upper()

            event_id = add_timeline_event(
                cur,
                case=case,
                event_type="TASK",
                event_title=(
                    "Task completed"
                    if status_upper
                    == "COMPLETED"
                    else "Task created"
                ),
                event_details=(
                    f"Task: {task_text or '-'}\n"
                    f"Assigned to: "
                    f"{assigned_to or '-'}\n"
                    f"Status: "
                    f"{task_status or '-'}"
                ),
                event_status=status_upper,
                event_category="tasks",
                source_type="TASK",
                source_id=str(task_id),
                event_at=(
                    completed_at
                    or created_at
                ),
                is_internal=True
            )

            if event_id:
                counts["tasks"] += 1

        cur.execute("""
            SELECT
                id,
                file_name,
                drive_file_link,
                uploaded_at
            FROM case_files
            WHERE
                LOWER(
                    TRIM(
                        COALESCE(
                            case_id,
                            ''
                        )
                    )
                )
                =
                LOWER(TRIM(%s))
            ORDER BY id ASC
        """, (
            canonical,
        ))

        for (
            file_id,
            file_name,
            drive_file_link,
            uploaded_at
        ) in cur.fetchall():
            event_id = add_timeline_event(
                cur,
                case=case,
                event_type="DOCUMENT",
                event_title="Document uploaded",
                event_details=(
                    f"File: {file_name or '-'}\n"
                    f"Drive: "
                    f"{drive_file_link or '-'}"
                ),
                event_status="UPLOADED",
                event_category="documents",
                source_type="CASE_FILE",
                source_id=str(file_id),
                event_at=uploaded_at,
                is_internal=True
            )

            if event_id:
                counts["files"] += 1

        if case.get("next_hearing"):
            event_id = add_timeline_event(
                cur,
                case=case,
                event_type="HEARING",
                event_title="Next hearing recorded",
                event_details=(
                    f"Date: "
                    f"{case['next_hearing']}\n"
                    f"Court: "
                    f"{case.get('court_name') or '-'}\n"
                    f"Judge: "
                    f"{case.get('judge_name') or '-'}"
                ),
                event_status="SCHEDULED",
                event_category="hearings",
                source_type="CASE_HEARING",
                source_id=(
                    f"{canonical}:"
                    f"{case['next_hearing']}"
                ),
                event_at=datetime.now(),
                is_internal=True
            )

            if event_id:
                counts["hearings"] += 1

        conn.commit()

        return counts

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def get_timeline(
    *,
    case_value: str,
    category: str = "all",
    include_cancelled: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            raise ValueError(
                f"Case not found: {case_value}"
            )

        conditions = ["""
            LOWER(
                TRIM(
                    COALESCE(
                        case_number,
                        case_id,
                        ''
                    )
                )
            )
            =
            LOWER(TRIM(%s))
        """]

        values: List[Any] = [
            case["canonical_case_id"]
        ]

        normalized_category = (
            category
            or "all"
        ).strip().lower()

        if normalized_category not in {
            "all",
            "communications",
            "hearings",
            "documents",
            "tasks",
            "client_updates",
            "notes",
        }:
            raise ValueError(
                "Unknown timeline filter. Use: "
                "all, communications, hearings, "
                "documents, tasks, client_updates, notes."
            )

        if normalized_category != "all":
            conditions.append(
                "LOWER(COALESCE(event_category, '')) = %s"
            )
            values.append(
                normalized_category
            )

        if not include_cancelled:
            conditions.append("""
                UPPER(
                    COALESCE(
                        event_status,
                        ''
                    )
                ) <> 'CANCELLED'
            """)

        values.append(limit)

        cur.execute(
            """
            SELECT
                id,
                event_type,
                event_title,
                event_details,
                event_status,
                event_category,
                source_type,
                source_id,
                event_at,
                created_by,
                is_internal
            FROM client_timeline
            WHERE
            """
            + " AND ".join(conditions)
            + """
            ORDER BY
                event_at DESC,
                id DESC
            LIMIT %s
            """,
            tuple(values)
        )

        rows = cur.fetchall()

        items = []

        for row in rows:
            (
                event_id,
                event_type,
                event_title,
                event_details,
                event_status,
                event_category,
                source_type,
                source_id,
                event_at,
                created_by,
                is_internal
            ) = row

            items.append({
                "id": event_id,
                "event_type": event_type,
                "event_title": event_title,
                "event_details": event_details,
                "event_status": event_status,
                "event_category": event_category,
                "source_type": source_type,
                "source_id": source_id,
                "event_at": event_at,
                "created_by": created_by,
                "is_internal": is_internal,
            })

        counts = Counter()

        for item in items:
            counts[
                item.get(
                    "event_category"
                )
                or "other"
            ] += 1

        return {
            "case": case,
            "items": items,
            "counts": dict(counts),
            "category": normalized_category,
            "include_cancelled": include_cancelled,
        }

    finally:
        cur.close()
        conn.close()
