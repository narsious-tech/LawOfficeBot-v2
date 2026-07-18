import os
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

import psycopg2

from config import DATABASE_URL
from services.activity_logger import (
    log_activity_with_cursor,
)

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
PRIORITY_ICONS = {
    "URGENT": "🔴",
    "HIGH": "🟠",
    "NORMAL": "🔵",
    "LOW": "⚪"
}


def normalize_priority(priority):
    value = (
        priority
        or "NORMAL"
    ).strip().upper()

    if value not in PRIORITY_ICONS:
        return "NORMAL"

    return value


def priority_icon(priority):
    return PRIORITY_ICONS[
        normalize_priority(priority)
    ]
async def taskdetails(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /taskdetails TASK_ID\n\n"
            "Example:\n"
            "/taskdetails 8"
        )
        return

    task_id = context.args[0].strip()

    if not task_id.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number.\n\n"
            "Example: /taskdetails 8"
        )
        return

    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id) == str(ADMIN_USER_ID)
    )
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Identify the staff member using Telegram
        
        requesting_staff = None

        if not is_admin:
            cur.execute("""
                SELECT staff_name
                FROM staff_accounts
                WHERE telegram_user_id = %s
                  AND is_active = TRUE
                LIMIT 1
            """, (
                telegram_user_id,
            ))

            staff_row = cur.fetchone()

            if not staff_row:
                await update.effective_message.reply_text(
                    "❌ Your Telegram account is not linked "
                    "with an active staff account."
                )
                return

            requesting_staff = staff_row[0]
        
        # Fetch complete task details
        cur.execute("""
            SELECT
                t.id,
                t.assigned_to,
                t.case_number,
                t.task,
                t.deadline,
                t.due_at,
                t.status,
                t.source_type,
                t.source_work_id,
                t.assigned_by,
                t.notes,
                t.created_at,
                t.completed_at,
                c.client_name,
                c.case_title,
                c.next_hearing,
                c.court_name,
                c.judge_name,
                COALESCE(
                    t.priority,
                    'NORMAL'
                ) AS priority
                
                FROM tasks t

            LEFT JOIN LATERAL (
                SELECT
                    client_name,
                    case_title,
                    next_hearing,
                    court_name,
                    judge_name
                FROM cases
                WHERE
                    t.case_number IS NOT NULL
                    AND TRIM(t.case_number) <> ''
                    AND (
                        LOWER(TRIM(case_number))
                            =
                        LOWER(TRIM(t.case_number))

                        OR

                        LOWER(TRIM(case_id))
                            =
                        LOWER(TRIM(t.case_number))
                    )
                ORDER BY id DESC
                LIMIT 1
            ) c
                ON TRUE

            WHERE t.id = %s
            LIMIT 1
        """, (
            int(task_id),
        ))

        row = cur.fetchone()

        if not row:
            await update.effective_message.reply_text(
                f"❌ Task #{task_id} not found."
            )
            return

        (
            db_task_id,
            assigned_to,
            case_number,
            task_text,
            deadline,
            due_at,
            status,
            source_type,
            source_work_id,
            assigned_by,
            notes,
            created_at,
            completed_at,
            client_name,
            mirrored_case_title,
            case_next_hearing,
            court_name,
            judge_name,
            priority
        ) = row
        
        # Staff can view only their own assigned task
        if not is_admin:
            if (
                not assigned_to
                or assigned_to.strip().lower()
                != requesting_staff.strip().lower()
            ):
                await update.effective_message.reply_text(
                    "❌ This task is not assigned to you."
                )
                return
        case_title = (
            notes
            or mirrored_case_title
            or ""
        )

        hearing_date = (
            deadline
            or case_next_hearing
        )

        if source_type == "advocate_diaries_work":
            source_label = "Advocate Diaries Work"

        elif source_type == "manual":
            source_label = "Manual Task"

        else:
            source_label = (
                source_type
                or "Not recorded"
            )

        def format_value(value):
            if value is None:
                return "-"

            return str(value)

        def format_datetime(value):
            if not value:
                return "-"

            try:
                return value.strftime(
                    "%d-%m-%Y %I:%M %p"
                )

            except Exception:
                return str(value)
        
        priority_value = normalize_priority(
            priority
        )

        priority_symbol = priority_icon(
            priority_value
        )
        
        message = (
            f"📋 TASK DETAILS\n\n"
            f"🆔 Task ID: #{db_task_id}\n"
            f"👤 Assigned To: {assigned_to}\n"
            f"📊 Status: {status}\n"
            f"{priority_symbol} Priority: "
            f"{priority_value}\n"
            f"📌 Source: {source_label}\n\n"
        )

        if client_name:
            message += (
                f"👤 Client: {client_name}\n"
            )

        if case_title:
            message += (
                f"⚖️ Case Title: {case_title}\n"
            )

        if case_number:
            message += (
                f"🔢 Case Number: {case_number}\n"
            )

        if court_name:
            message += (
                f"🏛 Court: {court_name}\n"
            )

        if judge_name:
            message += (
                f"⚖️ Judge: {judge_name}\n"
            )

        message += (
            f"\n📝 Task:\n"
            f"{task_text}\n"
        )

        if hearing_date:
            message += (
                f"\n📅 Next Hearing: "
                f"{format_value(hearing_date)}\n"
            )

        if due_at:
            message += (
                f"⏰ Internal Deadline: "
                f"{format_datetime(due_at)}\n"
            )

        message += (
            f"\n🕒 Assigned / Created At: "
            f"{format_datetime(created_at)}\n"
        )

        if completed_at:
            message += (
                f"✅ Completed At: "
                f"{format_datetime(completed_at)}\n"
            )

        if source_work_id:
            message += (
                f"\n🔗 AD Work ID:\n"
                f"{source_work_id}\n"
            )

        await update.effective_message.reply_text(
            message[:3900]
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Task details failed:\n"
            f"{type(e).__name__}: {e}"
        )

    finally:
        cur.close()
        conn.close()

async def taskhistory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id) == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/taskhistory STAFF_NAME\n"
            "/taskhistory STAFF_NAME pending\n"
            "/taskhistory STAFF_NAME completed\n"
            "/taskhistory STAFF_NAME 7days\n"
            "/taskhistory STAFF_NAME 30days\n\n"
            "Examples:\n"
            "/taskhistory Happy\n"
            "/taskhistory Happy pending\n"
            "/taskhistory Happy completed\n"
            "/taskhistory Happy 7days"
        )
        return

    allowed_filters = {
        "pending",
        "completed",
        "7days",
        "30days"
    }

    requested_filter = "all"

    staff_parts = list(context.args)

    last_argument = staff_parts[-1].lower()

    if last_argument in allowed_filters:
        requested_filter = last_argument
        staff_parts = staff_parts[:-1]

    staff_name_input = " ".join(
        staff_parts
    ).strip()

    if not staff_name_input:
        await update.effective_message.reply_text(
            "❌ Staff name is required."
        )
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Find staff in staff_accounts first

        cur.execute("""
            SELECT staff_name
            FROM staff_accounts
            WHERE LOWER(TRIM(staff_name))
                  = LOWER(TRIM(%s))
              AND is_active = TRUE
            LIMIT 1
        """, (
            staff_name_input,
        ))

        staff_row = cur.fetchone()

        if staff_row:
            staff_name = staff_row[0]

        else:
            # Fallback to staff table

            cur.execute("""
                SELECT name
                FROM staff
                WHERE LOWER(TRIM(name))
                      = LOWER(TRIM(%s))
                LIMIT 1
            """, (
                staff_name_input,
            ))

            staff_row = cur.fetchone()

            if not staff_row:
                await update.effective_message.reply_text(
                    f"❌ Staff member "
                    f"'{staff_name_input}' not found."
                )
                return

            staff_name = staff_row[0]

        # Build task filters

        where_conditions = [
            """
            LOWER(TRIM(t.assigned_to))
            = LOWER(TRIM(%s))
            """
        ]

        query_parameters = [
            staff_name
        ]

        if requested_filter == "pending":
            where_conditions.append(
                "UPPER(t.status) = 'PENDING'"
            )

        elif requested_filter == "completed":
            where_conditions.append(
                "UPPER(t.status) = 'COMPLETED'"
            )

        elif requested_filter == "7days":
            where_conditions.append("""
                COALESCE(
                    t.completed_at,
                    t.due_at,
                    t.created_at
                )
                >= CURRENT_TIMESTAMP
                   - INTERVAL '7 days'
            """)

        elif requested_filter == "30days":
            where_conditions.append("""
                COALESCE(
                    t.completed_at,
                    t.due_at,
                    t.created_at
                )
                >= CURRENT_TIMESTAMP
                   - INTERVAL '30 days'
            """)

        where_sql = " AND ".join(
            where_conditions
        )

        query = f"""
            SELECT
                t.id,
                t.case_number,
                t.task,
                t.deadline,
                t.due_at,
                t.status,
                t.source_type,
                t.source_work_id,
                t.notes,
                t.created_at,
                t.completed_at,
                c.client_name,
                c.case_title,
                c.court_name,
                c.judge_name,
                c.next_hearing,
                COALESCE(
                    t.priority,
                    'NORMAL'
                ) AS priority
                
                FROM tasks t

            LEFT JOIN LATERAL (
                SELECT
                    client_name,
                    case_title,
                    court_name,
                    judge_name,
                    next_hearing

                FROM cases

                WHERE
                    t.case_number IS NOT NULL

                    AND TRIM(
                        t.case_number
                    ) <> ''

                    AND (
                        LOWER(
                            TRIM(case_number)
                        )
                        =
                        LOWER(
                            TRIM(t.case_number)
                        )

                        OR

                        LOWER(
                            TRIM(case_id)
                        )
                        =
                        LOWER(
                            TRIM(t.case_number)
                        )
                    )

                ORDER BY id DESC

                LIMIT 1

            ) c
                ON TRUE

            WHERE {where_sql}

                ORDER BY

                CASE
                    WHEN UPPER(t.status)
                         = 'PENDING'
                    THEN 0
                    ELSE 1
                END,

                CASE UPPER(
                    COALESCE(
                        t.priority,
                        'NORMAL'
                    )
                )
                    WHEN 'URGENT' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'NORMAL' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,

                COALESCE(
                    t.due_at,
                    t.completed_at,
                    t.created_at
                ) DESC,

                t.id DESC
    """

        cur.execute(
            query,
            tuple(query_parameters)
        )

        rows = cur.fetchall()

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Task history failed:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"ℹ️ No {requested_filter} tasks found "
            f"for {staff_name}."
        )
        return

    # ----------------------------
    # DATE FORMATTERS
    # ----------------------------

    def format_datetime(value):
        if not value:
            return "-"

        if hasattr(value, "strftime"):
            try:
                return value.strftime(
                    "%d-%m-%Y %I:%M %p"
                )

            except Exception:
                pass

        text = str(value).strip()

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%d-%m-%Y %I:%M %p"
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(
                    text,
                    fmt
                )

                return parsed.strftime(
                    "%d-%m-%Y %I:%M %p"
                )

            except ValueError:
                continue

        return text

    def format_task_date(value):
        if not value:
            return "-"

        if hasattr(value, "strftime"):
            try:
                has_time = (
                    getattr(value, "hour", 0)
                    or getattr(value, "minute", 0)
                    or getattr(value, "second", 0)
                )

                if has_time:
                    return value.strftime(
                        "%d-%m-%Y %I:%M %p"
                    )

                return value.strftime(
                    "%d-%m-%Y"
                )

            except Exception:
                pass

        text = str(value).strip()

        datetime_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%d-%m-%Y %I:%M %p"
        ]

        for fmt in datetime_formats:
            try:
                parsed = datetime.strptime(
                    text,
                    fmt
                )

                return parsed.strftime(
                    "%d-%m-%Y %I:%M %p"
                )

            except ValueError:
                continue

        date_formats = [
            "%Y-%m-%d",
            "%d-%m-%Y"
        ]

        for fmt in date_formats:
            try:
                parsed = datetime.strptime(
                    text,
                    fmt
                )

                return parsed.strftime(
                    "%d-%m-%Y"
                )

            except ValueError:
                continue

        return text

    # ----------------------------
    # COUNTS
    # ----------------------------

    pending_count = sum(
        1
        for row in rows
        if str(row[5]).upper()
        == "PENDING"
    )

    completed_count = sum(
        1
        for row in rows
        if str(row[5]).upper()
        == "COMPLETED"
    )

    filter_labels = {
        "all": "ALL TASKS",
        "pending": "PENDING",
        "completed": "COMPLETED",
        "7days": "LAST 7 DAYS",
        "30days": "LAST 30 DAYS"
    }

    # ----------------------------
    # REPORT HEADER
    # ----------------------------

    message = (
        f"📚 TASK HISTORY — "
        f"{staff_name.upper()}\n"

        f"🔎 Filter: "
        f"{filter_labels[requested_filter]}\n\n"

        f"📌 Total Tasks: {len(rows)}\n"

        f"🟡 Pending: "
        f"{pending_count}\n"

        f"✅ Completed: "
        f"{completed_count}\n\n"
    )

    # ----------------------------
    # TASK DETAILS
    # ----------------------------

    for row in rows:

        (
            task_id,
            case_number,
            task_text,
            task_date,
            due_at,
            status,
            source_type,
            source_work_id,
            notes,
            created_at,
            completed_at,
            client_name,
            mirrored_case_title,
            court_name,
            judge_name,
            case_next_hearing,
            priority
        ) = row

        priority_value = normalize_priority(
            priority
        )

        priority_symbol = priority_icon(
            priority_value
        )
        
        # Use case table title first.
        # Use notes only as fallback.

        case_title = (
            mirrored_case_title
            or notes
            or ""
        )

        if source_type == "advocate_diaries_work":
            source_label = (
                "Advocate Diaries Work"
            )

        elif source_type == "manual":
            source_label = (
                "Manual Task"
            )

        else:
            source_label = (
                source_type
                or "Not recorded"
            )

        if (
            str(status).upper()
            == "COMPLETED"
        ):
            status_icon = "✅"

        else:
            status_icon = "🟡"

        message += (
            f"{status_icon} "
            f"Task #{task_id}\n"

            f"📊 Status: {status}\n"

            f"{priority_symbol} Priority: "
            f"{priority_value}\n"

            f"📌 Source: "
            f"{source_label}\n"
        )
            
        if client_name:
            message += (
                f"👤 Client: "
                f"{client_name}\n"
            )

        if case_title:
            message += (
                f"⚖️ Case Title: "
                f"{case_title}\n"
            )

        if case_number:
            message += (
                f"🔢 Case Number: "
                f"{case_number}\n"
            )

        if court_name:
            message += (
                f"🏛 Court: "
                f"{court_name}\n"
            )

        if judge_name:
            message += (
                f"⚖️ Judge: "
                f"{judge_name}\n"
            )

        message += (
            f"📝 Task: "
            f"{task_text}\n"
        )

        # ------------------------
        # AD WORK DATE
        # ------------------------

        if (
            source_type
            == "advocate_diaries_work"
        ):
            hearing_value = (
                task_date
                or case_next_hearing
            )

            if hearing_value:
                message += (
                    f"📅 Next Hearing: "
                    f"{format_task_date(hearing_value)}\n"
                )

        # ------------------------
        # MANUAL TASK DEADLINE
        # ------------------------

        else:
            manual_deadline = (
                due_at
                or task_date
            )

            if manual_deadline:
                message += (
                    f"⏰ Task Deadline: "
                    f"{format_task_date(manual_deadline)}\n"
                )

        message += (
            f"🕒 Assigned At: "
            f"{format_datetime(created_at)}\n"
        )

        if completed_at:
            message += (
                f"✅ Completed At: "
                f"{format_datetime(completed_at)}\n"
            )

        if source_work_id:
            message += (
                f"🔗 AD Work ID: "
                f"{source_work_id}\n"
            )

        message += (
            "\n──────────────\n\n"
        )

    # ----------------------------
    # SEND IN SAFE TELEGRAM CHUNKS
    # ----------------------------

    max_length = 3800

    while message:

        if len(message) <= max_length:
            chunk = message
            message = ""

        else:
            split_at = message.rfind(
                "\n\n",
                0,
                max_length
            )

            if split_at == -1:
                split_at = max_length

            chunk = message[:split_at]

            message = message[
                split_at:
            ].lstrip()

        await update.effective_message.reply_text(
            chunk
        )

def ensure_task_reassignment_table():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_reassignment_history
            (
                id SERIAL PRIMARY KEY,
                task_id INTEGER NOT NULL,
                old_assigned_to TEXT,
                new_assigned_to TEXT NOT NULL,
                reassigned_by BIGINT,
                reason TEXT,
                reassigned_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    finally:
        cur.close()
        conn.close()

def ensure_task_priority_column():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            ALTER TABLE tasks
            ADD COLUMN IF NOT EXISTS priority TEXT
            DEFAULT 'NORMAL'
        """)

        cur.execute("""
            UPDATE tasks
            SET priority = 'NORMAL'
            WHERE priority IS NULL
               OR TRIM(priority) = ''
        """)

        conn.commit()

    finally:
        cur.close()
        conn.close()

async def reassign_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id)
        == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/reassigntask TASK_ID NEW_STAFF\n\n"
            "With reason:\n"
            "/reassigntask TASK_ID NEW_STAFF | REASON\n\n"
            "Examples:\n"
            "/reassigntask 17 Jimmy\n"
            "/reassigntask 17 Jimmy | Urgent court filing"
        )
        return

    task_id_text = context.args[0].strip()

    if not task_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number."
        )
        return

    task_id = int(task_id_text)

    remaining_text = " ".join(
        context.args[1:]
    ).strip()

    reason = ""

    if "|" in remaining_text:
        staff_text, reason = remaining_text.split(
            "|",
            1
        )

        new_staff_input = staff_text.strip()
        reason = reason.strip()

    else:
        new_staff_input = remaining_text.strip()

    if not new_staff_input:
        await update.effective_message.reply_text(
            "❌ New staff name is required."
        )
        return

    ensure_task_reassignment_table()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    old_staff_telegram_id = None
    new_staff_telegram_id = None

    try:
        # Read the task and lock it during reassignment.
        cur.execute("""
            SELECT
                id,
                assigned_to,
                task,
                case_number,
                notes,
                status,
                source_type
            FROM tasks
            WHERE id = %s
            FOR UPDATE
        """, (
            task_id,
        ))

        task_row = cur.fetchone()

        if not task_row:
            await update.effective_message.reply_text(
                f"❌ Task #{task_id} not found."
            )
            return

        (
            db_task_id,
            old_staff_name,
            task_text,
            case_number,
            case_title,
            task_status,
            source_type
        ) = task_row

        if str(task_status).upper() == "COMPLETED":
            await update.effective_message.reply_text(
                f"❌ Task #{task_id} is already completed "
                "and cannot be reassigned."
            )
            return

        # Find the new staff member.
        cur.execute("""
            SELECT
                staff_name,
                telegram_user_id
            FROM staff_accounts
            WHERE LOWER(TRIM(staff_name))
                  = LOWER(TRIM(%s))
              AND is_active = TRUE
            ORDER BY created_at DESC
            LIMIT 1
        """, (
            new_staff_input,
        ))

        new_staff_row = cur.fetchone()

        if new_staff_row:
            new_staff_name = new_staff_row[0]
            new_staff_telegram_id = new_staff_row[1]

        else:
            # Fallback for a staff record not yet linked to Telegram.
            cur.execute("""
                SELECT name
                FROM staff
                WHERE LOWER(TRIM(name))
                      = LOWER(TRIM(%s))
                LIMIT 1
            """, (
                new_staff_input,
            ))

            fallback_staff = cur.fetchone()

            if not fallback_staff:
                await update.effective_message.reply_text(
                    f"❌ Staff member "
                    f"'{new_staff_input}' not found."
                )
                return

            new_staff_name = fallback_staff[0]
            new_staff_telegram_id = None

        if (
            old_staff_name
            and old_staff_name.strip().lower()
            == new_staff_name.strip().lower()
        ):
            await update.effective_message.reply_text(
                f"ℹ️ Task #{task_id} is already assigned "
                f"to {new_staff_name}."
            )
            return

        # Find old staff Telegram account for notification.
        if old_staff_name:
            cur.execute("""
                SELECT telegram_user_id
                FROM staff_accounts
                WHERE LOWER(TRIM(staff_name))
                      = LOWER(TRIM(%s))
                  AND is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, (
                old_staff_name,
            ))

            old_staff_row = cur.fetchone()

            if old_staff_row:
                old_staff_telegram_id = old_staff_row[0]

        # Update current assignee.
        cur.execute("""
            UPDATE tasks
            SET assigned_to = %s
            WHERE id = %s
              AND UPPER(status) = 'PENDING'
            RETURNING id
        """, (
            new_staff_name,
            task_id
        ))

        updated = cur.fetchone()

        if not updated:
            conn.rollback()

            await update.effective_message.reply_text(
                f"❌ Task #{task_id} could not be reassigned."
            )
            return

        # Record audit history.
        cur.execute("""
            INSERT INTO task_reassignment_history
            (
                task_id,
                old_assigned_to,
                new_assigned_to,
                reassigned_by,
                reason
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            task_id,
            old_staff_name,
            new_staff_name,
            telegram_user_id,
            reason or None
        ))

        log_activity_with_cursor(
            cur,
            case_value=case_number or "",
            event_code="TASK_REASSIGNED",
            details=(
                f"Task #{db_task_id}\n"
                f"From: {old_staff_name or '-'}\n"
                f"To: {new_staff_name}\n"
                f"Reason: {reason or '-'}\n"
                f"Task: {task_text or '-'}"
            ),
            source_module="TASK",
            source_id=str(db_task_id),
            user_id=telegram_user_id,
            metadata={
                "task_id": db_task_id,
                "old_assigned_to": old_staff_name,
                "new_assigned_to": new_staff_name,
                "reason": reason,
            }
        )

        conn.commit()

    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Task reassignment failed:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    source_label = (
        "Advocate Diaries Work"
        if source_type == "advocate_diaries_work"
        else "Manual Task"
    )

    confirmation = (
        f"✅ TASK REASSIGNED\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Previous Staff: "
        f"{old_staff_name or 'Unassigned'}\n"
        f"👤 New Staff: {new_staff_name}\n"
        f"📌 Source: {source_label}\n"
    )

    if case_title:
        confirmation += (
            f"⚖️ {case_title}\n"
        )

    if case_number:
        confirmation += (
            f"🔢 {case_number}\n"
        )

    confirmation += (
        f"📝 {task_text}\n"
    )

    if reason:
        confirmation += (
            f"💬 Reason: {reason}\n"
        )

    await update.effective_message.reply_text(
        confirmation[:3900]
    )

    # Notify previous assignee.
    if (
        old_staff_telegram_id
        and str(old_staff_telegram_id)
        != str(new_staff_telegram_id)
    ):
        try:
            await context.bot.send_message(
                chat_id=old_staff_telegram_id,
                text=(
                    f"↪️ TASK REASSIGNED\n\n"
                    f"Task #{task_id} has been removed "
                    f"from your pending task list.\n\n"
                    f"👤 Reassigned To: {new_staff_name}\n"
                    f"📝 {task_text}"
                    + (
                        f"\n💬 Reason: {reason}"
                        if reason
                        else ""
                    )
                )
            )

        except Exception as e:
            await update.effective_message.reply_text(
                f"⚠️ Task was reassigned, but notification "
                f"to {old_staff_name} failed:\n"
                f"{type(e).__name__}: {e}"
            )

    # Notify new assignee.
    if new_staff_telegram_id:
        try:
            await context.bot.send_message(
                chat_id=new_staff_telegram_id,
                text=(
                    f"📌 REASSIGNED TASK RECEIVED\n\n"
                    f"🆔 Task #{task_id}\n"
                    f"👤 Previous Staff: "
                    f"{old_staff_name or 'Unassigned'}\n"
                    f"📌 Source: {source_label}\n"
                    + (
                        f"⚖️ {case_title}\n"
                        if case_title
                        else ""
                    )
                    + (
                        f"🔢 {case_number}\n"
                        if case_number
                        else ""
                    )
                    + f"📝 {task_text}\n"
                    + (
                        f"💬 Reason: {reason}\n"
                        if reason
                        else ""
                    )
                    + "\n"
                    + f"Use /taskdetails {task_id}\n"
                    + f"Use /completetask {task_id} "
                    + "after completion."
                )
            )

        except Exception as e:
            await update.effective_message.reply_text(
                f"⚠️ Task was reassigned, but notification "
                f"to {new_staff_name} failed:\n"
                f"{type(e).__name__}: {e}"
            )


async def reassign_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id)
        == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/reassignhistory TASK_ID\n\n"
            "Example:\n"
            "/reassignhistory 17"
        )
        return

    task_id_text = context.args[0].strip()

    if not task_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number."
        )
        return

    task_id = int(task_id_text)

    ensure_task_reassignment_table()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                t.task,
                t.case_number,
                t.assigned_to,
                t.status,
                h.old_assigned_to,
                h.new_assigned_to,
                h.reassigned_by,
                h.reason,
                h.reassigned_at
            FROM tasks t

            LEFT JOIN task_reassignment_history h
                ON h.task_id = t.id

            WHERE t.id = %s

            ORDER BY h.reassigned_at ASC NULLS LAST,
                     h.id ASC NULLS LAST
        """, (
            task_id,
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"❌ Task #{task_id} not found."
        )
        return

    task_text = rows[0][0]
    case_number = rows[0][1]
    current_assignee = rows[0][2]
    status = rows[0][3]

    history_rows = [
        row for row in rows
        if row[4] is not None
        or row[5] is not None
    ]

    message = (
        f"🔄 TASK REASSIGNMENT HISTORY\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Current Assignee: "
        f"{current_assignee or 'Unassigned'}\n"
        f"📊 Status: {status}\n"
    )

    if case_number:
        message += (
            f"🔢 {case_number}\n"
        )

    message += (
        f"📝 {task_text}\n\n"
    )

    if not history_rows:
        message += (
            "ℹ️ This task has not been reassigned."
        )

    else:
        message += (
            f"📌 Reassignments: "
            f"{len(history_rows)}\n\n"
        )

        for index, row in enumerate(
            history_rows,
            start=1
        ):
            (
                _,
                _,
                _,
                _,
                old_assigned_to,
                new_assigned_to,
                reassigned_by,
                reason,
                reassigned_at
            ) = row

            try:
                formatted_time = (
                    reassigned_at.strftime(
                        "%d-%m-%Y %I:%M %p"
                    )
                )
            except Exception:
                formatted_time = str(
                    reassigned_at
                )

            message += (
                f"{index}. "
                f"{old_assigned_to or 'Unassigned'} "
                f"→ {new_assigned_to}\n"
                f"🕒 {formatted_time}\n"
                f"👤 Reassigned By: "
                f"{reassigned_by}\n"
            )

            if reason:
                message += (
                    f"💬 Reason: {reason}\n"
                )

            message += "\n"

    await update.effective_message.reply_text(
        message[:3900]
    )

def ensure_task_reopen_table():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_reopen_history
            (
                id SERIAL PRIMARY KEY,
                task_id INTEGER NOT NULL,
                reopened_by BIGINT,
                previous_completed_at TIMESTAMP,
                reason TEXT,
                reopened_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    finally:
        cur.close()
        conn.close()

async def reopen_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id)
        == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/reopentask TASK_ID | REASON\n\n"
            "Example:\n"
            "/reopentask 8 | Reply requires correction"
        )
        return

    full_text = " ".join(context.args).strip()

    if "|" in full_text:
        task_id_text, reason = full_text.split(
            "|",
            1
        )

        task_id_text = task_id_text.strip()
        reason = reason.strip()

    else:
        task_id_text = full_text.strip()
        reason = ""

    if not task_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number."
        )
        return

    if not reason:
        await update.effective_message.reply_text(
            "❌ A reason is required.\n\n"
            "Example:\n"
            "/reopentask 8 | Reply requires correction"
        )
        return

    task_id = int(task_id_text)

    ensure_task_reopen_table()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    assigned_staff_telegram_id = None

    try:
        cur.execute("""
            SELECT
                id,
                assigned_to,
                task,
                case_number,
                notes,
                status,
                source_type,
                source_work_id,
                completed_at
            FROM tasks
            WHERE id = %s
            FOR UPDATE
        """, (
            task_id,
        ))

        task_row = cur.fetchone()

        if not task_row:
            await update.effective_message.reply_text(
                f"❌ Task #{task_id} not found."
            )
            return

        (
            db_task_id,
            assigned_to,
            task_text,
            case_number,
            case_title,
            current_status,
            source_type,
            source_work_id,
            previous_completed_at
        ) = task_row

        if str(current_status).upper() != "COMPLETED":
            await update.effective_message.reply_text(
                f"ℹ️ Task #{task_id} is not completed.\n"
                f"Current status: {current_status}"
            )
            return

        cur.execute("""
            UPDATE tasks
            SET
                status = 'PENDING',
                completed_at = NULL
            WHERE id = %s
              AND UPPER(status) = 'COMPLETED'
            RETURNING id
        """, (
            task_id,
        ))

        reopened = cur.fetchone()

        if not reopened:
            conn.rollback()

            await update.effective_message.reply_text(
                f"❌ Task #{task_id} could not be reopened."
            )
            return

        cur.execute("""
            INSERT INTO task_reopen_history
            (
                task_id,
                reopened_by,
                previous_completed_at,
                reason
            )
            VALUES (%s, %s, %s, %s)
        """, (
            task_id,
            telegram_user_id,
            previous_completed_at,
            reason
        ))

        if assigned_to:
            cur.execute("""
                SELECT telegram_user_id
                FROM staff_accounts
                WHERE LOWER(TRIM(staff_name))
                      = LOWER(TRIM(%s))
                  AND is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, (
                assigned_to,
            ))

            staff_row = cur.fetchone()

            if staff_row:
                assigned_staff_telegram_id = (
                    staff_row[0]
                )

        log_activity_with_cursor(
            cur,
            case_value=case_number or "",
            event_code="TASK_REOPENED",
            details=(
                f"Task #{db_task_id}\n"
                f"Reopened by user: {telegram_user_id}\n"
                f"Task: {task_text or '-'}"
            ),
            source_module="TASK",
            source_id=str(db_task_id),
            user_id=telegram_user_id,
            metadata={
                "task_id": db_task_id,
                "reopened_by": telegram_user_id,
            }
        )

        conn.commit()

    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Task reopening failed:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if source_type == "advocate_diaries_work":
        source_label = "Advocate Diaries Work"
        sync_note = (
            "\n\n⚠️ Advocate Diaries Work was previously "
            "completed and has not been reopened there."
        )

    elif source_type == "manual":
        source_label = "Manual Task"
        sync_note = ""

    else:
        source_label = source_type or "Task"
        sync_note = ""

    confirmation = (
        f"🔄 TASK REOPENED\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Assigned To: "
        f"{assigned_to or 'Unassigned'}\n"
        f"📌 Source: {source_label}\n"
    )

    if case_title:
        confirmation += (
            f"⚖️ {case_title}\n"
        )

    if case_number:
        confirmation += (
            f"🔢 {case_number}\n"
        )

    confirmation += (
        f"📝 {task_text}\n"
        f"💬 Reason: {reason}\n"
        f"📊 New Status: PENDING"
        f"{sync_note}"
    )

    await update.effective_message.reply_text(
        confirmation[:3900]
    )

    if assigned_staff_telegram_id:
        try:
            await context.bot.send_message(
                chat_id=assigned_staff_telegram_id,
                text=(
                    f"🔄 TASK REOPENED\n\n"
                    f"🆔 Task #{task_id}\n"
                    f"📝 {task_text}\n"
                    f"💬 Reason: {reason}\n"
                    f"📊 Status: PENDING\n\n"
                    f"Use /taskdetails {task_id}\n"
                    f"Use /completetask {task_id} "
                    f"after correction."
                    f"{sync_note}"
                )
            )

        except Exception as e:
            await update.effective_message.reply_text(
                f"⚠️ Task was reopened, but notification "
                f"to {assigned_to} failed:\n"
                f"{type(e).__name__}: {e}"
            )

async def reopen_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id)
        == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/reopenhistory TASK_ID\n\n"
            "Example:\n"
            "/reopenhistory 8"
        )
        return

    task_id_text = context.args[0].strip()

    if not task_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number."
        )
        return

    task_id = int(task_id_text)

    ensure_task_reopen_table()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                t.task,
                t.case_number,
                t.assigned_to,
                t.status,
                h.reopened_by,
                h.previous_completed_at,
                h.reason,
                h.reopened_at
            FROM tasks t

            LEFT JOIN task_reopen_history h
                ON h.task_id = t.id

            WHERE t.id = %s

            ORDER BY
                h.reopened_at ASC NULLS LAST,
                h.id ASC NULLS LAST
        """, (
            task_id,
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"❌ Task #{task_id} not found."
        )
        return

    task_text = rows[0][0]
    case_number = rows[0][1]
    assigned_to = rows[0][2]
    status = rows[0][3]

    history_rows = [
        row
        for row in rows
        if row[4] is not None
        or row[7] is not None
    ]

    message = (
        f"🔄 TASK REOPEN HISTORY\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Assigned To: "
        f"{assigned_to or 'Unassigned'}\n"
        f"📊 Current Status: {status}\n"
    )

    if case_number:
        message += (
            f"🔢 {case_number}\n"
        )

    message += (
        f"📝 {task_text}\n\n"
    )

    if not history_rows:
        message += (
            "ℹ️ This task has never been reopened."
        )

    else:
        message += (
            f"📌 Reopen Events: "
            f"{len(history_rows)}\n\n"
        )

        for index, row in enumerate(
            history_rows,
            start=1
        ):
            (
                _,
                _,
                _,
                _,
                reopened_by,
                previous_completed_at,
                reason,
                reopened_at
            ) = row

            try:
                reopened_time = (
                    reopened_at.strftime(
                        "%d-%m-%Y %I:%M %p"
                    )
                )
            except Exception:
                reopened_time = str(
                    reopened_at
                )

            try:
                previous_completion = (
                    previous_completed_at.strftime(
                        "%d-%m-%Y %I:%M %p"
                    )
                )
            except Exception:
                previous_completion = (
                    str(previous_completed_at)
                    if previous_completed_at
                    else "-"
                )

            message += (
                f"{index}. Reopened\n"
                f"🕒 Reopened At: "
                f"{reopened_time}\n"
                f"👤 Reopened By: "
                f"{reopened_by}\n"
                f"✅ Previous Completion: "
                f"{previous_completion}\n"
                f"💬 Reason: {reason or '-'}\n\n"
            )

    await update.effective_message.reply_text(
        message[:3900]
    )

async def set_task_priority(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    is_admin = (
        ADMIN_USER_ID
        and str(telegram_user_id)
        == str(ADMIN_USER_ID)
    )

    if not is_admin:
        await update.effective_message.reply_text(
            "❌ This command is available only to the admin."
        )
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/setpriority TASK_ID PRIORITY\n\n"
            "Available priorities:\n"
            "URGENT\n"
            "HIGH\n"
            "NORMAL\n"
            "LOW\n\n"
            "Example:\n"
            "/setpriority 17 URGENT"
        )
        return

    task_id_text = context.args[0].strip()
    priority = context.args[1].strip().upper()

    if not task_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ TASK_ID must be a number."
        )
        return

    allowed_priorities = {
        "URGENT",
        "HIGH",
        "NORMAL",
        "LOW"
    }

    if priority not in allowed_priorities:
        await update.effective_message.reply_text(
            "❌ Invalid priority.\n\n"
            "Use one of:\n"
            "URGENT, HIGH, NORMAL, LOW"
        )
        return

    task_id = int(task_id_text)

    ensure_task_priority_column()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                assigned_to,
                task,
                case_number,
                status,
                priority
            FROM tasks
            WHERE id = %s
            LIMIT 1
        """, (
            task_id,
        ))

        task_row = cur.fetchone()

        if not task_row:
            await update.effective_message.reply_text(
                f"❌ Task #{task_id} not found."
            )
            return

        (
            assigned_to,
            task_text,
            case_number,
            status,
            old_priority
        ) = task_row

        old_priority = (
            old_priority
            or "NORMAL"
        ).upper()

        if old_priority == priority:
            await update.effective_message.reply_text(
                f"ℹ️ Task #{task_id} is already "
                f"{priority} priority."
            )
            return

        cur.execute("""
            UPDATE tasks
            SET priority = %s
            WHERE id = %s
        """, (
            priority,
            task_id
        ))

        staff_telegram_user_id = None

        if assigned_to:
            cur.execute("""
                SELECT telegram_user_id
                FROM staff_accounts
                WHERE LOWER(TRIM(staff_name))
                      =
                      LOWER(TRIM(%s))
                  AND is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, (
                assigned_to,
            ))

            staff_row = cur.fetchone()

            if staff_row:
                staff_telegram_user_id = (
                    staff_row[0]
                )

        log_activity_with_cursor(
            cur,
            case_value=case_number or "",
            event_code="TASK_PRIORITY_CHANGED",
            details=(
                f"Task #{db_task_id}\n"
                f"Priority changed to: {priority_value}\n"
                f"Task: {task_text or '-'}"
            ),
            source_module="TASK",
            source_id=str(db_task_id),
            user_id=telegram_user_id,
            metadata={
                "task_id": db_task_id,
                "priority": priority_value,
            }
        )

        conn.commit()

    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Priority update failed:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    priority_icons = {
        "URGENT": "🔴",
        "HIGH": "🟠",
        "NORMAL": "🔵",
        "LOW": "⚪"
    }

    icon = priority_icons[priority]

    message = (
        f"{icon} TASK PRIORITY UPDATED\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Assigned To: "
        f"{assigned_to or 'Unassigned'}\n"
        f"📊 Status: {status}\n"
        f"📌 Previous Priority: {old_priority}\n"
        f"{icon} New Priority: {priority}\n"
    )

    if case_number:
        message += (
            f"🔢 {case_number}\n"
        )

    message += (
        f"📝 {task_text}"
    )

    await update.effective_message.reply_text(
        message
    )

    if staff_telegram_user_id:
        try:
            await context.bot.send_message(
                chat_id=staff_telegram_user_id,
                text=(
                    f"{icon} TASK PRIORITY CHANGED\n\n"
                    f"🆔 Task #{task_id}\n"
                    f"{icon} Priority: {priority}\n"
                    f"📝 {task_text}\n\n"
                    f"Use /taskdetails {task_id}"
                )
            )

        except Exception as e:
            await update.effective_message.reply_text(
                f"⚠️ Priority was updated, but "
                f"staff notification failed:\n"
                f"{type(e).__name__}: {e}"
            )

