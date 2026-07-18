import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import psycopg2
from telegram import Update
from telegram.ext import ContextTypes

from config import DATABASE_URL


IST = ZoneInfo("Asia/Kolkata")

OFFICE_GROUP_CHAT_ID = os.getenv("OFFICE_GROUP_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

OFFICE_START_TIME = os.getenv(
    "ATTENDANCE_OFFICE_START_TIME",
    "09:30"
)

OFFICE_END_TIME = os.getenv(
    "ATTENDANCE_OFFICE_END_TIME",
    "18:30"
)

FORGOT_CHECKOUT_TIME = os.getenv(
    "ATTENDANCE_FORGOT_CHECKOUT_TIME",
    "20:30"
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def now_ist():
    return datetime.now(IST)


def format_time(value):
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime("%I:%M %p")

    return str(value)


def format_date(value):
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y")

    return str(value)


def format_minutes(minutes):
    if minutes is None:
        return "-"

    minutes = max(0, int(minutes))
    hours = minutes // 60
    remaining = minutes % 60

    return f"{hours}h {remaining}m"


def parse_clock(value, fallback):
    try:
        hour_text, minute_text = value.split(":", 1)

        return time(
            hour=int(hour_text),
            minute=int(minute_text),
            tzinfo=IST
        )

    except Exception:
        return fallback


def time_to_minutes(value):
    return value.hour * 60 + value.minute


def calculate_late_minutes(checkin_time):
    if not checkin_time:
        return 0

    expected = parse_clock(
        OFFICE_START_TIME,
        time(hour=9, minute=30, tzinfo=IST)
    )

    actual_minutes = (
        checkin_time.hour * 60
        + checkin_time.minute
    )

    expected_minutes = time_to_minutes(
        expected
    )

    return max(
        0,
        actual_minutes - expected_minutes
    )


def calculate_early_minutes(checkout_time):
    if not checkout_time:
        return 0

    expected = parse_clock(
        OFFICE_END_TIME,
        time(hour=18, minute=30, tzinfo=IST)
    )

    actual_minutes = (
        checkout_time.hour * 60
        + checkout_time.minute
    )

    expected_minutes = time_to_minutes(
        expected
    )

    return max(
        0,
        expected_minutes - actual_minutes
    )



async def send_long_message(
    update: Update,
    message: str
):
    max_length = 3800
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            chunk = remaining
            remaining = ""

        else:
            split_at = remaining.rfind(
                "\n\n",
                0,
                max_length
            )

            if split_at == -1:
                split_at = max_length

            chunk = remaining[:split_at]
            remaining = remaining[
                split_at:
            ].lstrip()

        await update.effective_message.reply_text(
            chunk
        )

async def sync_today_attendance_sessions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    today = now_ist().date()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            WITH location_events AS (
                SELECT
                    al.telegram_user_id,
                    al.staff_name,
                    al.action,

                    (
                        al.created_at
                        AT TIME ZONE 'UTC'
                        AT TIME ZONE 'Asia/Kolkata'
                    ) AS local_created_at,

                    COALESCE(
                        al.office_id,
                        nearest_office.office_id
                    ) AS resolved_office_id,

                    COALESCE(
                        al.office_name,
                        nearest_office.office_name
                    ) AS resolved_office_name

                FROM attendance_locations al

                LEFT JOIN LATERAL (
                    SELECT
                        ao.id AS office_id,
                        ao.office_name

                    FROM attendance_offices ao

                    WHERE ao.is_active = TRUE

                    ORDER BY (
                        6371000 * 2 * ASIN(
                            SQRT(
                                POWER(
                                    SIN(
                                        RADIANS(
                                            ao.latitude
                                            - CAST(
                                                al.latitude
                                                AS DOUBLE PRECISION
                                            )
                                        ) / 2
                                    ),
                                    2
                                )
                                +
                                COS(
                                    RADIANS(
                                        CAST(
                                            al.latitude
                                            AS DOUBLE PRECISION
                                        )
                                    )
                                )
                                *
                                COS(
                                    RADIANS(
                                        ao.latitude
                                    )
                                )
                                *
                                POWER(
                                    SIN(
                                        RADIANS(
                                            ao.longitude
                                            - CAST(
                                                al.longitude
                                                AS DOUBLE PRECISION
                                            )
                                        ) / 2
                                    ),
                                    2
                                )
                            )
                        )
                    ) ASC

                    LIMIT 1
                ) nearest_office
                    ON TRUE

                WHERE (
                    al.created_at
                    AT TIME ZONE 'UTC'
                    AT TIME ZONE 'Asia/Kolkata'
                )::date = %s
            )

            SELECT
                telegram_user_id,
                MAX(staff_name) AS staff_name,

                MIN(local_created_at) FILTER (
                    WHERE action = 'CHECKIN'
                ) AS checkin_time,

                (
                    ARRAY_AGG(
                        resolved_office_id
                        ORDER BY local_created_at ASC
                    ) FILTER (
                        WHERE action = 'CHECKIN'
                    )
                )[1] AS checkin_office_id,

                (
                    ARRAY_AGG(
                        resolved_office_name
                        ORDER BY local_created_at ASC
                    ) FILTER (
                        WHERE action = 'CHECKIN'
                    )
                )[1] AS checkin_office_name,

                MAX(local_created_at) FILTER (
                    WHERE action = 'CHECKOUT'
                ) AS checkout_time,

                (
                    ARRAY_AGG(
                        resolved_office_id
                        ORDER BY local_created_at DESC
                    ) FILTER (
                        WHERE action = 'CHECKOUT'
                    )
                )[1] AS checkout_office_id,

                (
                    ARRAY_AGG(
                        resolved_office_name
                        ORDER BY local_created_at DESC
                    ) FILTER (
                        WHERE action = 'CHECKOUT'
                    )
                )[1] AS checkout_office_name

            FROM location_events

            GROUP BY telegram_user_id

            ORDER BY staff_name
        """, (
            today,
        ))

        rows = cur.fetchall()

        synced = 0
        skipped = 0

        for (
            telegram_user_id,
            staff_name,
            checkin_time,
            checkin_office_id,
            checkin_office_name,
            checkout_time,
            checkout_office_id,
            checkout_office_name
        ) in rows:

            if not checkin_time:
                skipped += 1
                continue

            if checkout_time:
                working_minutes = max(
                    0,
                    int(
                        (
                            checkout_time
                            - checkin_time
                        ).total_seconds()
                        // 60
                    )
                )

                status = "CLOSED"

            else:
                working_minutes = None
                status = "OPEN"

            cur.execute("""
                INSERT INTO attendance_sessions
                (
                    telegram_user_id,
                    staff_name,
                    attendance_date,

                    checkin_time,
                    checkin_office_id,
                    checkin_office_name,

                    checkout_time,
                    checkout_office_id,
                    checkout_office_name,

                    status,
                    working_minutes,

                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )

                ON CONFLICT (
                    telegram_user_id,
                    attendance_date
                )

                DO UPDATE SET
                    staff_name = EXCLUDED.staff_name,

                    checkin_time =
                        EXCLUDED.checkin_time,

                    checkin_office_id =
                        COALESCE(
                            EXCLUDED.checkin_office_id,
                            attendance_sessions.checkin_office_id
                        ),

                    checkin_office_name =
                        COALESCE(
                            EXCLUDED.checkin_office_name,
                            attendance_sessions.checkin_office_name
                        ),

                    checkout_time =
                        COALESCE(
                            EXCLUDED.checkout_time,
                            attendance_sessions.checkout_time
                        ),

                    checkout_office_id =
                        COALESCE(
                            EXCLUDED.checkout_office_id,
                            attendance_sessions.checkout_office_id
                        ),

                    checkout_office_name =
                        COALESCE(
                            EXCLUDED.checkout_office_name,
                            attendance_sessions.checkout_office_name
                        ),

                    status = CASE
                        WHEN EXCLUDED.checkout_time
                             IS NOT NULL
                        THEN 'CLOSED'
                        ELSE 'OPEN'
                    END,

                    working_minutes =
                        COALESCE(
                            EXCLUDED.working_minutes,
                            attendance_sessions.working_minutes
                        ),

                    updated_at =
                        CURRENT_TIMESTAMP
            """, (
                telegram_user_id,
                staff_name,
                today,

                checkin_time,
                checkin_office_id,
                checkin_office_name,

                checkout_time,
                checkout_office_id,
                checkout_office_name,

                status,
                working_minutes
            ))

            synced += 1

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Attendance session sync failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    await update.effective_message.reply_text(
        "✅ TODAY'S ATTENDANCE SESSIONS SYNCED\n\n"
        f"📅 Date: "
        f"{today.strftime('%d-%m-%Y')}\n"
        f"✅ Synced: {synced}\n"
        f"⏭ Skipped: {skipped}\n\n"
        "Run /attendancetoday to verify."
    )    
async def whoinoffice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    today = now_ist().date()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                staff_name,
                checkin_time,
                checkin_office_name,
                checkout_time,
                checkout_office_name,
                status,
                working_minutes
            FROM attendance_sessions
            WHERE attendance_date = %s
            ORDER BY
                CASE
                    WHEN status = 'OPEN'
                    THEN 0
                    ELSE 1
                END,
                checkin_time ASC NULLS LAST,
                staff_name ASC
        """, (
            today,
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "📍 No attendance sessions found for today."
        )
        return

    open_rows = [
        row
        for row in rows
        if row[5] == "OPEN"
    ]

    closed_rows = [
        row
        for row in rows
        if row[5] == "CLOSED"
    ]

    message = (
        "🏢 STAFF ATTENDANCE STATUS\n"
        f"📅 {today.strftime('%d-%m-%Y')}\n\n"
        f"🟢 Currently Working: {len(open_rows)}\n"
        f"🔴 Checked Out: {len(closed_rows)}\n\n"
    )

    if open_rows:
        message += "🟢 CURRENTLY WORKING\n\n"

        for (
            staff_name,
            checkin_time,
            checkin_office_name,
            checkout_time,
            checkout_office_name,
            status,
            working_minutes
        ) in open_rows:

            current_minutes = int(
                (
                    now_ist().replace(
                        tzinfo=None
                    )
                    - checkin_time
                ).total_seconds()
                // 60
            )

            message += (
                f"👤 {staff_name}\n"
                f"🏢 {checkin_office_name or '-'}\n"
                f"🕒 In: {format_time(checkin_time)}\n"
                f"⏱ Working: "
                f"{format_minutes(current_minutes)}\n\n"
            )

    if closed_rows:
        message += "🔴 CHECKED OUT\n\n"

        for (
            staff_name,
            checkin_time,
            checkin_office_name,
            checkout_time,
            checkout_office_name,
            status,
            working_minutes
        ) in closed_rows:

            message += (
                f"👤 {staff_name}\n"
                f"🟢 In: "
                f"{format_time(checkin_time)} "
                f"({checkin_office_name or '-'})\n"
                f"🔴 Out: "
                f"{format_time(checkout_time)} "
                f"({checkout_office_name or '-'})\n"
                f"⏱ Total: "
                f"{format_minutes(working_minutes)}\n\n"
            )

    await send_long_message(
        update,
        message
    )


async def attendancetoday(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    today = now_ist().date()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                staff_name,
                checkin_time,
                checkin_office_name,
                checkout_time,
                checkout_office_name,
                status,
                working_minutes
            FROM attendance_sessions
            WHERE attendance_date = %s
            ORDER BY
                checkin_time ASC NULLS LAST,
                staff_name ASC
        """, (
            today,
        ))

        rows = cur.fetchall()

        cur.execute("""
            SELECT DISTINCT ON (
                LOWER(TRIM(staff_name))
            )
                staff_name
            FROM staff_accounts
            WHERE is_active = TRUE
            ORDER BY
                LOWER(TRIM(staff_name)),
                staff_name
        """)

        all_staff = [
            row[0]
            for row in cur.fetchall()
        ]

    finally:
        cur.close()
        conn.close()

    attended_staff = {
        row[0].strip().lower()
        for row in rows
        if row[0]
    }

    absent_staff = [
        staff_name
        for staff_name in all_staff
        if staff_name.strip().lower()
        not in attended_staff
    ]

    present_count = len(rows)
    open_count = sum(
        1
        for row in rows
        if row[5] == "OPEN"
    )

    closed_count = sum(
        1
        for row in rows
        if row[5] == "CLOSED"
    )

    late_count = sum(
        1
        for row in rows
        if calculate_late_minutes(
            row[1]
        ) > 0
    )

    early_count = sum(
        1
        for row in rows
        if row[3]
        and calculate_early_minutes(
            row[3]
        ) > 0
    )

    message = (
        "📅 TODAY'S ATTENDANCE\n"
        f"📆 {today.strftime('%d-%m-%Y')}\n\n"
        f"✅ Present: {present_count}\n"
        f"🟢 Working Now: {open_count}\n"
        f"🔴 Checked Out: {closed_count}\n"
        f"🟠 Late Arrivals: {late_count}\n"
        f"⚠️ Early Checkouts: {early_count}\n"
        f"❌ Absent: {len(absent_staff)}\n\n"
    )

    if rows:
        message += "👥 STAFF DETAILS\n\n"

        for (
            staff_name,
            checkin_time,
            checkin_office_name,
            checkout_time,
            checkout_office_name,
            status,
            working_minutes
        ) in rows:

            late_minutes = calculate_late_minutes(
                checkin_time
            )

            early_minutes = calculate_early_minutes(
                checkout_time
            )

            status_icon = (
                "🟢"
                if status == "OPEN"
                else "✅"
            )

            message += (
                f"{status_icon} {staff_name}\n"
                f"🟢 In: "
                f"{format_time(checkin_time)} "
                f"({checkin_office_name or '-'})\n"
            )

            if checkout_time:
                message += (
                    f"🔴 Out: "
                    f"{format_time(checkout_time)} "
                    f"({checkout_office_name or '-'})\n"
                    f"⏱ Total: "
                    f"{format_minutes(working_minutes)}\n"
                )

            else:
                message += (
                    "🔴 Out: Pending\n"
                )

            if late_minutes:
                message += (
                    f"🟠 Late by: "
                    f"{late_minutes} minutes\n"
                )

            if early_minutes:
                message += (
                    f"⚠️ Early by: "
                    f"{early_minutes} minutes\n"
                )

            message += "\n"

    if absent_staff:
        message += "❌ ABSENT STAFF\n\n"

        for staff_name in absent_staff:
            message += (
                f"• {staff_name}\n"
            )

    await send_long_message(
        update,
        message
    )


async def staffattendance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/staffattendance STAFF_NAME [DAYS]\n\n"
            "Examples:\n"
            "/staffattendance Happy\n"
            "/staffattendance Happy 30"
        )
        return

    days = 30

    if context.args[-1].isdigit():
        days = max(
            1,
            min(
                int(context.args[-1]),
                365
            )
        )

        staff_name = " ".join(
            context.args[:-1]
        ).strip()

    else:
        staff_name = " ".join(
            context.args
        ).strip()

    if not staff_name:
        await update.effective_message.reply_text(
            "❌ Staff name is required."
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                attendance_date,
                checkin_time,
                checkin_office_name,
                checkout_time,
                checkout_office_name,
                status,
                working_minutes
            FROM attendance_sessions
            WHERE LOWER(TRIM(staff_name))
                  = LOWER(TRIM(%s))
              AND attendance_date
                  >= CURRENT_DATE - (%s - 1)
            ORDER BY attendance_date DESC
        """, (
            staff_name,
            days
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"📅 No attendance found for "
            f"{staff_name} in the last {days} days."
        )
        return

    total_minutes = sum(
        int(row[6] or 0)
        for row in rows
    )

    late_days = sum(
        1
        for row in rows
        if calculate_late_minutes(
            row[1]
        ) > 0
    )

    early_days = sum(
        1
        for row in rows
        if row[3]
        and calculate_early_minutes(
            row[3]
        ) > 0
    )

    open_sessions = sum(
        1
        for row in rows
        if row[5] == "OPEN"
    )

    message = (
        "📊 STAFF ATTENDANCE REPORT\n\n"
        f"👤 Staff: {staff_name}\n"
        f"📅 Period: Last {days} days\n"
        f"✅ Attendance Days: {len(rows)}\n"
        f"🟠 Late Days: {late_days}\n"
        f"⚠️ Early Checkout Days: {early_days}\n"
        f"🔴 Open Sessions: {open_sessions}\n"
        f"⏱ Total Working Time: "
        f"{format_minutes(total_minutes)}\n\n"
    )

    for (
        attendance_date,
        checkin_time,
        checkin_office_name,
        checkout_time,
        checkout_office_name,
        status,
        working_minutes
    ) in rows:

        message += (
            f"📅 {format_date(attendance_date)}\n"
            f"🟢 {format_time(checkin_time)} "
            f"({checkin_office_name or '-'})\n"
            f"🔴 {format_time(checkout_time)} "
            f"({checkout_office_name or '-'})\n"
            f"⏱ {format_minutes(working_minutes)}\n"
        )

        late_minutes = calculate_late_minutes(
            checkin_time
        )

        if late_minutes:
            message += (
                f"🟠 Late: "
                f"{late_minutes} minutes\n"
            )

        message += "\n"

    await send_long_message(
        update,
        message
    )


async def forgot_checkout_job(
    context: ContextTypes.DEFAULT_TYPE
):
    today = now_ist().date()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                telegram_user_id,
                staff_name,
                checkin_time,
                checkin_office_name
            FROM attendance_sessions
            WHERE attendance_date = %s
              AND status = 'OPEN'
              AND checkout_time IS NULL
            ORDER BY checkin_time ASC
        """, (
            today,
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        return

    group_chat_id = (
        OFFICE_GROUP_CHAT_ID
        or ADMIN_CHAT_ID
    )

    for (
        telegram_user_id,
        staff_name,
        checkin_time,
        checkin_office_name
    ) in rows:

        message = (
            "⚠️ CHECKOUT REMINDER\n\n"
            f"👤 Staff: {staff_name}\n"
            f"🟢 Checked in: "
            f"{format_time(checkin_time)}\n"
            f"🏢 Office: "
            f"{checkin_office_name or '-'}\n\n"
            "Your attendance session is still open.\n"
            "Please use /checkout in the private bot."
        )

        try:
            await context.bot.send_message(
                chat_id=telegram_user_id,
                text=message
            )

        except Exception as exc:
            print(
                "PRIVATE CHECKOUT REMINDER FAILED: "
                f"{staff_name}: "
                f"{type(exc).__name__}: {exc}"
            )

        if group_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=group_chat_id,
                    text=message
                )

            except Exception as exc:
                print(
                    "GROUP CHECKOUT REMINDER FAILED: "
                    f"{staff_name}: "
                    f"{type(exc).__name__}: {exc}"
                )


async def daily_attendance_summary_job(
    context: ContextTypes.DEFAULT_TYPE
):
    today = now_ist().date()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                staff_name,
                checkin_time,
                checkin_office_name,
                checkout_time,
                checkout_office_name,
                status,
                working_minutes
            FROM attendance_sessions
            WHERE attendance_date = %s
            ORDER BY staff_name ASC
        """, (
            today,
        ))

        rows = cur.fetchall()

        cur.execute("""
            SELECT staff_name
            FROM staff_accounts
            WHERE is_active = TRUE
            ORDER BY staff_name ASC
        """)

        all_staff = [
            row[0]
            for row in cur.fetchall()
        ]

    finally:
        cur.close()
        conn.close()

    attended = {
        row[0].strip().lower()
        for row in rows
        if row[0]
    }

    absent_staff = [
        name
        for name in all_staff
        if name.strip().lower()
        not in attended
    ]

    message = (
        "📊 DAILY ATTENDANCE SUMMARY\n"
        f"📅 {today.strftime('%d-%m-%Y')}\n\n"
        f"✅ Present: {len(rows)}\n"
        f"❌ Absent: {len(absent_staff)}\n\n"
    )

    for (
        staff_name,
        checkin_time,
        checkin_office_name,
        checkout_time,
        checkout_office_name,
        status,
        working_minutes
    ) in rows:

        message += (
            f"👤 {staff_name}\n"
            f"🟢 In: "
            f"{format_time(checkin_time)} "
            f"({checkin_office_name or '-'})\n"
            f"🔴 Out: "
            f"{format_time(checkout_time)} "
            f"({checkout_office_name or '-'})\n"
            f"⏱ Total: "
            f"{format_minutes(working_minutes)}\n"
        )

        late_minutes = calculate_late_minutes(
            checkin_time
        )

        if late_minutes:
            message += (
                f"🟠 Late by: "
                f"{late_minutes} minutes\n"
            )

        if status == "OPEN":
            message += (
                "⚠️ Checkout pending\n"
            )

        message += "\n"

    if absent_staff:
        message += "❌ ABSENT\n\n"

        for staff_name in absent_staff:
            message += (
                f"• {staff_name}\n"
            )

    chat_id = (
        OFFICE_GROUP_CHAT_ID
        or ADMIN_CHAT_ID
    )

    if not chat_id:
        return

    max_length = 3800
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            chunk = remaining
            remaining = ""

        else:
            split_at = remaining.rfind(
                "\n\n",
                0,
                max_length
            )

            if split_at == -1:
                split_at = max_length

            chunk = remaining[:split_at]
            remaining = remaining[
                split_at:
            ].lstrip()

        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk
        )


async def test_forgot_checkout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await forgot_checkout_job(
        context
    )

    await update.effective_message.reply_text(
        "✅ Forgot-checkout reminder test completed."
    )


async def test_attendance_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await daily_attendance_summary_job(
        context
    )

    await update.effective_message.reply_text(
        "✅ Attendance summary test completed."
    )
