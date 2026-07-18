from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2

from config import DATABASE_URL
from services.communication_service import (
    get_db_connection,
    get_case_record,
    get_client_record,
    resolve_client_mobile,
    normalize_mobile,
    get_office_profile,
    make_communication_ref,
    display_mobile,
    format_date,
    office_signature,
    create_message_log,
)

try:
    from services.client_timeline import (
        log_communication_event,
    )
except Exception:
    log_communication_event = None


REMINDER_DAYS = {
    7: "7-day reminder",
    3: "3-day reminder",
    1: "Tomorrow's hearing reminder",
    0: "Today's hearing reminder",
}


def ensure_hearing_automation_tables():
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hearing_reminder_queue (
                id SERIAL PRIMARY KEY,

                case_db_id INTEGER,
                case_number TEXT NOT NULL,
                client_name TEXT,
                phone_number TEXT,

                hearing_date DATE NOT NULL,
                reminder_days INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,

                message_text TEXT NOT NULL,
                communication_ref TEXT,

                queue_status TEXT
                    DEFAULT 'PENDING',

                client_message_id INTEGER,

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                approved_at TIMESTAMP,
                sent_at TIMESTAMP,
                cancelled_at TIMESTAMP,

                approved_by BIGINT,
                sent_by BIGINT,

                error_message TEXT
            )
        """)

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            hearing_reminder_queue_unique_idx
            ON hearing_reminder_queue (
                case_number,
                hearing_date,
                reminder_days
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            hearing_reminder_queue_status_idx
            ON hearing_reminder_queue (
                queue_status,
                hearing_date
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS hearing_update_queue (
                id SERIAL PRIMARY KEY,

                case_db_id INTEGER,
                case_number TEXT NOT NULL,
                client_name TEXT,
                phone_number TEXT,

                old_hearing_date DATE,
                new_hearing_date DATE,

                court_name TEXT,
                judge_name TEXT,
                case_status TEXT,
                update_note TEXT,

                message_text TEXT NOT NULL,
                communication_ref TEXT,

                queue_status TEXT
                    DEFAULT 'PENDING',

                client_message_id INTEGER,

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,

                approved_at TIMESTAMP,
                sent_at TIMESTAMP,
                cancelled_at TIMESTAMP,

                approved_by BIGINT,
                sent_by BIGINT,

                error_message TEXT
            )
        """)

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            hearing_update_queue_unique_idx
            ON hearing_update_queue (
                case_number,
                old_hearing_date,
                new_hearing_date
            )
        """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def format_hearing_date_long(value) -> str:
    if not value:
        return "Not presently available"

    if hasattr(value, "strftime"):
        return value.strftime("%d %B %Y")

    return format_date(value)


def build_hearing_reminder_message(
    case: Dict[str, Any],
    profile: Dict[str, str],
    reminder_days: int
) -> str:
    hearing_date = case.get("next_hearing")

    if reminder_days == 0:
        opening = (
            "This is a reminder that your matter is "
            "listed before the Court today."
        )
    elif reminder_days == 1:
        opening = (
            "This is a reminder that your matter is "
            "listed before the Court tomorrow."
        )
    else:
        opening = (
            "This is an advance reminder regarding the "
            "next hearing in your matter."
        )

    return (
        f"Dear {case.get('client_name') or 'Client'},\n\n"
        "⚖️ HEARING REMINDER\n\n"
        f"{opening}\n\n"
        f"⚖️ Case Number: "
        f"{case.get('canonical_case_id') or '-'}\n"
        f"📋 Case Title: "
        f"{case.get('case_title') or '-'}\n"
        f"🏛 Court: "
        f"{case.get('court_name') or '-'}\n"
        f"👨‍⚖️ Judge: "
        f"{case.get('judge_name') or '-'}\n"
        f"📅 Hearing Date: "
        f"{format_hearing_date_long(hearing_date)}\n\n"
        "Kindly remain available on your registered "
        "mobile number in case the office needs any "
        "clarification or document on the hearing date.\n\n"
        "Please mention the above case number whenever "
        "you contact the office regarding this matter.\n\n"
        f"{office_signature(profile)}"
    )


def build_post_hearing_update_message(
    case: Dict[str, Any],
    profile: Dict[str, str],
    *,
    old_hearing_date=None,
    new_hearing_date=None,
    update_note: str = ""
) -> str:
    note = (
        update_note.strip()
        if update_note
        else (
            "The matter was taken up before the Court. "
            "The next date has been recorded in our "
            "office system."
        )
    )

    message = (
        f"Dear {case.get('client_name') or 'Client'},\n\n"
        "⚖️ CASE HEARING UPDATE\n\n"
        f"⚖️ Case Number: "
        f"{case.get('canonical_case_id') or '-'}\n"
        f"📋 Case Title: "
        f"{case.get('case_title') or '-'}\n"
        f"🏛 Court: "
        f"{case.get('court_name') or '-'}\n"
        f"👨‍⚖️ Judge: "
        f"{case.get('judge_name') or '-'}\n\n"
        f"📝 Update:\n{note}\n"
    )

    if old_hearing_date:
        message += (
            f"\n📅 Previous Hearing: "
            f"{format_hearing_date_long(old_hearing_date)}\n"
        )

    if new_hearing_date:
        message += (
            f"📅 Next Hearing: "
            f"{format_hearing_date_long(new_hearing_date)}\n"
        )

    message += (
        "\nPlease contact the office if you require any "
        "clarification regarding this update.\n\n"
        "Please mention the above case number whenever "
        "you contact the office.\n\n"
        f"{office_signature(profile)}"
    )

    return message


def get_cases_for_reminder_date(
    cur,
    target_date: date
) -> List[Dict[str, Any]]:
    """
    Load cases for one hearing date without relying on PostgreSQL DateStyle.

    The existing cases table contains mixed hearing-date values such as:
        2026-06-15
        15-06-2026
        15/06/2026
        timestamps

    This query parses only recognised formats and ignores invalid values.
    """
    cur.execute("""
        WITH normalized_cases AS (
            SELECT
                c.*,

                CASE
                    WHEN TRIM(
                        COALESCE(
                            c.next_hearing::text,
                            ''
                        )
                    ) ~
                    '^\\d{4}-\\d{2}-\\d{2}'
                    THEN TO_DATE(
                        SUBSTRING(
                            TRIM(c.next_hearing::text)
                            FROM 1 FOR 10
                        ),
                        'YYYY-MM-DD'
                    )

                    WHEN TRIM(
                        COALESCE(
                            c.next_hearing::text,
                            ''
                        )
                    ) ~
                    '^\\d{1,2}-\\d{1,2}-\\d{4}$'
                    THEN TO_DATE(
                        TRIM(c.next_hearing::text),
                        'DD-MM-YYYY'
                    )

                    WHEN TRIM(
                        COALESCE(
                            c.next_hearing::text,
                            ''
                        )
                    ) ~
                    '^\\d{1,2}/\\d{1,2}/\\d{4}$'
                    THEN TO_DATE(
                        TRIM(c.next_hearing::text),
                        'DD/MM/YYYY'
                    )

                    WHEN TRIM(
                        COALESCE(
                            c.hearing_date::text,
                            ''
                        )
                    ) ~
                    '^\\d{4}-\\d{2}-\\d{2}'
                    THEN TO_DATE(
                        SUBSTRING(
                            TRIM(c.hearing_date::text)
                            FROM 1 FOR 10
                        ),
                        'YYYY-MM-DD'
                    )

                    WHEN TRIM(
                        COALESCE(
                            c.hearing_date::text,
                            ''
                        )
                    ) ~
                    '^\\d{1,2}-\\d{1,2}-\\d{4}$'
                    THEN TO_DATE(
                        TRIM(c.hearing_date::text),
                        'DD-MM-YYYY'
                    )

                    WHEN TRIM(
                        COALESCE(
                            c.hearing_date::text,
                            ''
                        )
                    ) ~
                    '^\\d{1,2}/\\d{1,2}/\\d{4}$'
                    THEN TO_DATE(
                        TRIM(c.hearing_date::text),
                        'DD/MM/YYYY'
                    )

                    ELSE NULL
                END AS normalized_hearing_date

            FROM cases c
        )

        SELECT
            id,
            case_id,
            case_number,
            case_title,
            client_name,
            mobile,
            case_type,
            court_name,
            opposite_party,
            normalized_hearing_date,
            status,
            judge_name,
            client_id,
            ad_client_id,
            client_verification_status,
            client_verification_sent_at,
            client_verified_at,
            client_correction_note

        FROM normalized_cases

        WHERE normalized_hearing_date = %s

        ORDER BY
            court_name ASC,
            judge_name ASC,
            case_number ASC
    """, (
        target_date,
    ))

    rows = cur.fetchall()

    keys = [
        "id",
        "case_id",
        "case_number",
        "case_title",
        "client_name",
        "mobile",
        "case_type",
        "court_name",
        "opposite_party",
        "next_hearing",
        "status",
        "judge_name",
        "client_id",
        "ad_client_id",
        "verification_status",
        "verification_sent_at",
        "verified_at",
        "correction_note",
    ]

    cases = []

    for row in rows:
        item = dict(zip(keys, row))
        item["canonical_case_id"] = (
            item.get("case_number")
            or item.get("case_id")
        )
        cases.append(item)

    return cases



def resolve_hearing_mobile(
    cur,
    case: Dict[str, Any],
    client: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Return the best available WhatsApp number for a hearing reminder.

    Lookup order:
    1. Standard Communication Centre resolver.
    2. Mobile on the current case.
    3. Mobile on another case with the same local client_id.
    4. Mobile on another case with the same Advocate Diaries client ID.
    5. Mobile on another case with the same normalized client name.
    6. Mobile/WhatsApp number from the clients table where available.
    """

    try:
        mobile = resolve_client_mobile(
            case,
            client
        )

        if mobile:
            return normalize_mobile(
                str(mobile)
            )

    except Exception:
        pass

    current_mobile = case.get("mobile")

    if current_mobile:
        try:
            return normalize_mobile(
                str(current_mobile)
            )
        except ValueError:
            pass

    where_parts = []
    values = []

    if case.get("id"):
        where_parts.append("id = %s")
        values.append(case["id"])

    if case.get("client_id"):
        where_parts.append("client_id = %s")
        values.append(case["client_id"])

    if case.get("ad_client_id"):
        where_parts.append("ad_client_id = %s")
        values.append(case["ad_client_id"])

    client_name = str(
        case.get("client_name")
        or ""
    ).strip()

    if client_name:
        where_parts.append(
            "LOWER(TRIM(COALESCE(client_name, ''))) "
            "= LOWER(TRIM(%s))"
        )
        values.append(client_name)

    if where_parts:
        cur.execute(
            """
            SELECT mobile
            FROM cases
            WHERE (
            """
            + " OR ".join(where_parts)
            + """
            )
              AND TRIM(
                    COALESCE(
                        mobile::text,
                        ''
                    )
                  ) <> ''
            ORDER BY
                CASE
                    WHEN id = %s THEN 0
                    ELSE 1
                END,
                id DESC
            LIMIT 100
            """,
            tuple(values + [case.get("id")])
        )

        for row in cur.fetchall():
            candidate = row[0]

            if not candidate:
                continue

            try:
                return normalize_mobile(
                    str(candidate)
                )
            except ValueError:
                continue

    try:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'clients'
        """)

        client_columns = {
            row[0]
            for row in cur.fetchall()
        }

        candidate_columns = [
            column
            for column in (
                "whatsapp_number",
                "mobile",
                "phone",
                "phone_number",
                "contact_number",
            )
            if column in client_columns
        ]

        client_filters = []
        client_values = []

        if (
            case.get("client_id")
            and "id" in client_columns
        ):
            client_filters.append("id = %s")
            client_values.append(case["client_id"])

        if (
            case.get("ad_client_id")
            and "ad_client_id" in client_columns
        ):
            client_filters.append("ad_client_id = %s")
            client_values.append(case["ad_client_id"])

        if (
            client_name
            and "client_name" in client_columns
        ):
            client_filters.append(
                "LOWER(TRIM(COALESCE(client_name, ''))) "
                "= LOWER(TRIM(%s))"
            )
            client_values.append(client_name)

        if candidate_columns and client_filters:
            cur.execute(
                "SELECT "
                + ", ".join(candidate_columns)
                + " FROM clients WHERE "
                + " OR ".join(client_filters)
                + " ORDER BY id DESC LIMIT 20",
                tuple(client_values)
            )

            for row in cur.fetchall():
                for candidate in row:
                    if not candidate:
                        continue

                    try:
                        return normalize_mobile(
                            str(candidate)
                        )
                    except ValueError:
                        continue

    except Exception:
        pass

    return None


def generate_hearing_reminder_queue(
    *,
    today: Optional[date] = None
) -> Dict[str, int]:
    today = today or date.today()

    conn = get_db_connection()
    cur = conn.cursor()

    counts = {
        "created": 0,
        "existing": 0,
        "missing_mobile": 0,
        "cases_checked": 0,
    }

    try:
        profile = get_office_profile(cur)

        for reminder_days in sorted(
            REMINDER_DAYS.keys(),
            reverse=True
        ):
            target_date = (
                today
                + timedelta(days=reminder_days)
            )

            cases = get_cases_for_reminder_date(
                cur,
                target_date
            )

            counts["cases_checked"] += len(cases)

            for case in cases:
                client = get_client_record(
                    cur,
                    case
                )

                mobile = resolve_hearing_mobile(
                    cur,
                    case,
                    client
                )

                if not mobile:
                    counts["missing_mobile"] += 1
                    continue

                case["mobile"] = mobile

                communication_ref = (
                    make_communication_ref()
                )

                message_text = (
                    build_hearing_reminder_message(
                        case,
                        profile,
                        reminder_days
                    )
                )

                cur.execute("""
                    INSERT INTO hearing_reminder_queue
                    (
                        case_db_id,
                        case_number,
                        client_name,
                        phone_number,

                        hearing_date,
                        reminder_days,
                        reminder_type,

                        message_text,
                        communication_ref,

                        queue_status
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        'PENDING'
                    )
                    ON CONFLICT (
                        case_number,
                        hearing_date,
                        reminder_days
                    )
                    DO NOTHING
                    RETURNING id
                """, (
                    case.get("id"),
                    case.get("canonical_case_id"),
                    case.get("client_name"),
                    mobile,

                    target_date,
                    reminder_days,
                    REMINDER_DAYS[reminder_days],

                    message_text,
                    communication_ref,
                ))

                inserted = cur.fetchone()

                if inserted:
                    counts["created"] += 1
                else:
                    counts["existing"] += 1

        conn.commit()

        return counts

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def get_pending_hearing_reminders(
    limit: int = 100
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                id,
                case_number,
                client_name,
                phone_number,
                hearing_date,
                reminder_days,
                reminder_type,
                communication_ref,
                created_at
            FROM hearing_reminder_queue
            WHERE queue_status = 'PENDING'
            ORDER BY
                hearing_date ASC,
                reminder_days ASC,
                id ASC
            LIMIT %s
        """, (
            limit,
        ))

        rows = cur.fetchall()

        keys = [
            "id",
            "case_number",
            "client_name",
            "phone_number",
            "hearing_date",
            "reminder_days",
            "reminder_type",
            "communication_ref",
            "created_at",
        ]

        return [
            dict(zip(keys, row))
            for row in rows
        ]

    finally:
        cur.close()
        conn.close()


def get_hearing_reminder(
    reminder_id: int
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                id,
                case_db_id,
                case_number,
                client_name,
                phone_number,
                hearing_date,
                reminder_days,
                reminder_type,
                message_text,
                communication_ref,
                queue_status,
                client_message_id
            FROM hearing_reminder_queue
            WHERE id = %s
            LIMIT 1
        """, (
            reminder_id,
        ))

        row = cur.fetchone()

        if not row:
            return None

        keys = [
            "id",
            "case_db_id",
            "case_number",
            "client_name",
            "phone_number",
            "hearing_date",
            "reminder_days",
            "reminder_type",
            "message_text",
            "communication_ref",
            "queue_status",
            "client_message_id",
        ]

        return dict(zip(keys, row))

    finally:
        cur.close()
        conn.close()


def approve_hearing_reminder(
    *,
    reminder_id: int,
    approved_by: int
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                case_number,
                phone_number,
                message_text,
                communication_ref,
                queue_status
            FROM hearing_reminder_queue
            WHERE id = %s
            FOR UPDATE
        """, (
            reminder_id,
        ))

        row = cur.fetchone()

        if not row:
            return None

        (
            case_number,
            phone_number,
            message_text,
            communication_ref,
            queue_status
        ) = row

        case = get_case_record(
            cur,
            case_number
        )

        if not case:
            raise ValueError(
                f"Case not found: {case_number}"
            )

        client = get_client_record(
            cur,
            case
        )

        client_message_id = create_message_log(
            cur,
            case=case,
            client=client,
            phone_number=phone_number,
            message_type="HEARING_REMINDER",
            message_text=message_text,
            sent_by=approved_by,
            communication_ref=communication_ref,
            template_name="hearing_reminder"
        )

        cur.execute("""
            UPDATE hearing_reminder_queue
            SET
                queue_status = 'APPROVED',
                approved_at = CURRENT_TIMESTAMP,
                approved_by = %s,
                client_message_id = %s
            WHERE id = %s
        """, (
            approved_by,
            client_message_id,
            reminder_id
        ))

        if log_communication_event:
            log_communication_event(
                cur,
                case=case,
                message_id=client_message_id,
                message_type="HEARING_REMINDER",
                delivery_status="DRAFT",
                communication_ref=communication_ref,
                created_by=approved_by
            )

        conn.commit()

        return get_hearing_reminder(
            reminder_id
        )

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def cancel_hearing_reminder(
    *,
    reminder_id: int,
    cancelled_by: int
) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE hearing_reminder_queue
            SET
                queue_status = 'CANCELLED',
                cancelled_at = CURRENT_TIMESTAMP,
                approved_by = %s
            WHERE id = %s
              AND queue_status IN (
                    'PENDING',
                    'APPROVED'
              )
        """, (
            cancelled_by,
            reminder_id
        ))

        updated = (
            cur.rowcount > 0
        )

        conn.commit()

        return updated

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()