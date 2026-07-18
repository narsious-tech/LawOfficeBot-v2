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
            SELECT
                staff_name
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
