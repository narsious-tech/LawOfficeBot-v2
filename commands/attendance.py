from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)

from telegram.ext import (
    ContextTypes,
    MessageHandler,
    filters
)
from bs4 import BeautifulSoup
from advocate_web import AdvocateWeb

web = AdvocateWeb()
import psycopg2
from config import DATABASE_URL
import os
from datetime import datetime
from bs4 import BeautifulSoup

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")


async def monitor_attendance_job(context):
    date = datetime.today().strftime("%Y-%m-%d")

    response = web.attendance(date)
    soup = BeautifulSoup(response.text, "lxml")

    tbody = soup.find("tbody")
    if tbody is None:
        return

    rows = tbody.find_all("tr")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    for row in rows:
        cols = row.find_all("td")

        staff_cell = cols[0]
        email_div = staff_cell.find("div", class_="table_col_sub_text")
        if email_div:
            email_div.extract()

        staff_name = staff_cell.get_text(" ", strip=True)
        attendance_date = cols[1].get_text(" ", strip=True)
        in_time = cols[2].get_text(" ", strip=True) or "-"
        out_time = cols[3].get_text(" ", strip=True) or "-"
        approval_status = cols[5].get_text(" ", strip=True)

        approve = row.find("a", class_="approve_attendance")
        attendance_id = approve["data-id"] if approve else row.get("id")

        cur.execute(
            "SELECT attendance_id, in_time, out_time FROM attendance_notifications WHERE attendance_id=%s",
            (attendance_id,)
        )
        old = cur.fetchone()

        if old is None:
            cur.execute("""
                INSERT INTO attendance_notifications
                (attendance_id, staff_name, attendance_date, in_time, out_time, approval_status)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (attendance_id, staff_name, attendance_date, in_time, out_time, approval_status))

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🟢 Attendance Marked\n\n"
                    f"👤 {staff_name}\n"
                    f"📅 {attendance_date}\n"
                    f"🟢 In: {in_time}\n"
                    f"🔴 Out: {out_time}\n"
                    f"✅ Status: {approval_status}"
                )
            )

        else:
            _, old_in, old_out = old

            if old_out != out_time and out_time != "-":
                cur.execute("""
                    UPDATE attendance_notifications
                    SET out_time=%s, approval_status=%s, last_seen=CURRENT_TIMESTAMP
                    WHERE attendance_id=%s
                """, (out_time, approval_status, attendance_id))

                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"🔴 Staff Checked Out\n\n"
                        f"👤 {staff_name}\n"
                        f"📅 {attendance_date}\n"
                        f"🔴 Out: {out_time}"
                    )
                )

    conn.commit()
    cur.close()
    conn.close()

async def linkstaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage:\n/linkstaff STAFF_NAME EMAIL PASSWORD"
        )
        return

    telegram_user_id = update.effective_user.id
    staff_name = context.args[0]
    ad_email = context.args[1]
    ad_password = " ".join(context.args[2:])

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO staff_accounts
        (telegram_user_id, staff_name, ad_email, ad_password)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (telegram_user_id)
        DO UPDATE SET
            staff_name = EXCLUDED.staff_name,
            ad_email = EXCLUDED.ad_email,
            ad_password = EXCLUDED.ad_password,
            is_active = TRUE
    """, (telegram_user_id, staff_name, ad_email, ad_password))

    conn.commit()
    cur.close()
    conn.close()

    await update.effective_message.reply_text(
        f"✅ Staff account linked for {staff_name}"
    )


def _admin_identifiers():
    """Return configured Telegram administrator identifiers as strings."""
    return {
        str(value).strip()
        for value in (ADMIN_USER_ID, ADMIN_CHAT_ID)
        if value is not None and str(value).strip()
    }


def _is_staff_admin(update: Update) -> bool:
    """
    Permit staff-management commands only for the configured administrator.

    ADMIN_USER_ID is preferred. ADMIN_CHAT_ID is also accepted for compatibility
    with the existing Railway environment.
    """
    allowed = _admin_identifiers()

    user_id = str(update.effective_user.id) if update.effective_user else ""
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""

    return bool(allowed) and (
        user_id in allowed
        or chat_id in allowed
    )


async def linkedstaff(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """List staff accounts currently linked to Telegram."""
    if not _is_staff_admin(update):
        await update.effective_message.reply_text(
            "❌ This command is restricted to the administrator."
        )
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                telegram_user_id,
                staff_name,
                ad_email,
                is_active
            FROM staff_accounts
            WHERE telegram_user_id IS NOT NULL
              AND COALESCE(is_active, TRUE) = TRUE
            ORDER BY
                is_active DESC,
                LOWER(staff_name)
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "No Telegram-linked staff accounts were found."
        )
        return

    lines = ["👥 LINKED STAFF ACCOUNTS", ""]

    for index, (
        telegram_user_id,
        staff_name,
        ad_email,
        is_active,
    ) in enumerate(rows, start=1):
        status = "Active" if is_active else "Inactive"

        lines.extend([
            f"{index}. {staff_name}",
            f"   Telegram ID: {telegram_user_id}",
            f"   Email: {ad_email or '-'}",
            f"   Status: {status}",
            "",
        ])

    lines.extend([
        "To de-link an account:",
        "/delinkstaff TELEGRAM_USER_ID",
        "",
        "You may also use the exact email or exact staff name.",
    ])

    message = "\n".join(lines)

    for start in range(0, len(message), 3900):
        await update.effective_message.reply_text(
            message[start:start + 3900]
        )


async def delinkstaff(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    De-link one staff account from Telegram.

    Accepted target:
      - Telegram user ID
      - Exact Advocate Diaries email
      - Exact staff name
    """
    if not _is_staff_admin(update):
        await update.effective_message.reply_text(
            "❌ This command is restricted to the administrator."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/delinkstaff TELEGRAM_USER_ID\n\n"
            "You may also use an exact email or exact staff name.\n"
            "Run /linkedstaff to see linked accounts."
        )
        return

    target = " ".join(context.args).strip()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        if target.lstrip("-").isdigit():
            cur.execute("""
                SELECT
                    telegram_user_id,
                    staff_name,
                    ad_email
                FROM staff_accounts
                WHERE telegram_user_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
            """, (int(target),))

        elif "@" in target:
            cur.execute("""
                SELECT
                    telegram_user_id,
                    staff_name,
                    ad_email
                FROM staff_accounts
                WHERE LOWER(ad_email) = LOWER(%s)
                  AND telegram_user_id IS NOT NULL
                  AND COALESCE(is_active, TRUE) = TRUE
            """, (target,))

        else:
            cur.execute("""
                SELECT
                    telegram_user_id,
                    staff_name,
                    ad_email
                FROM staff_accounts
                WHERE LOWER(staff_name) = LOWER(%s)
                  AND telegram_user_id IS NOT NULL
                  AND COALESCE(is_active, TRUE) = TRUE
            """, (target,))

        matches = cur.fetchall()

        if not matches:
            await update.effective_message.reply_text(
                "❌ No linked staff account matched that value.\n"
                "Run /linkedstaff and use the displayed Telegram ID."
            )
            return

        if len(matches) > 1:
            ids = ", ".join(str(row[0]) for row in matches)

            await update.effective_message.reply_text(
                "❌ More than one staff account matched that name.\n"
                f"Use one of these Telegram IDs: {ids}"
            )
            return

        telegram_user_id, staff_name, ad_email = matches[0]

        cur.execute("""
            UPDATE staff_accounts
            SET is_active = FALSE
            WHERE telegram_user_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
        """, (telegram_user_id,))

        if cur.rowcount != 1:
            conn.rollback()
            await update.effective_message.reply_text(
                "❌ Staff account was not de-linked because its active record changed.\n"
                "Run /linkedstaff and try again with the displayed Telegram ID."
            )
            return

        conn.commit()

    except Exception as exc:
        conn.rollback()
        print(f"STAFF DE-LINK FAILED: {type(exc).__name__}: {exc}")
        await update.effective_message.reply_text(
            "❌ Staff de-link failed safely. No account was changed.\n\n"
            f"Reason: {type(exc).__name__}\n"
            "Please check the Railway logs or ask the administrator to retry."
        )
        return

    finally:
        cur.close()
        conn.close()

    await update.effective_message.reply_text(
        "✅ Staff account de-linked.\n\n"
        f"Staff: {staff_name}\n"
        f"Email: {ad_email or '-'}\n"
        f"Former Telegram ID: {telegram_user_id}\n\n"
        "The Telegram link is now inactive. Historical credentials were retained.\nThe staff member can be linked again later using /linkstaff."
    )

def save_attendance_location(
    staff_name,
    telegram_user_id,
    action,
    latitude,
    longitude
):
    map_link = (
        f"https://www.google.com/maps?q="
        f"{latitude},{longitude}"
    )

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO attendance_locations
            (
                staff_name,
                telegram_user_id,
                action,
                latitude,
                longitude,
                map_link
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            staff_name,
            telegram_user_id,
            action,
            str(latitude),
            str(longitude),
            map_link
        ))

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()

    return map_link
    
async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        date = "today"
    else:
        date = context.args[0]

    if date == "today":
        from datetime import datetime
        date = datetime.today().strftime("%Y-%m-%d")

    response = web.attendance(date)
    soup = BeautifulSoup(response.text, "lxml")

    no_data = soup.find("span", class_="no-data-text")
    if no_data:
        await update.message.reply_text(f"❌ No attendance found for {date}")
        return

    tbody = soup.find("tbody")
    if tbody is None:
        await update.effective_message.reply_text(
            "Unable to read attendance."
        )
    rows = tbody.find_all("tr")
    context.user_data["attendance_ids"] = {}

    message = f"📅 Attendance ({date})\n\n"

    for i, row in enumerate(rows, start=1):
        cols = row.find_all("td")

        staff_cell = cols[0]
        email_div = staff_cell.find("div", class_="table_col_sub_text")
        email = email_div.get_text(strip=True) if email_div else ""

        if email_div:
            email_div.extract()

        staff = staff_cell.get_text(" ", strip=True)

        attendance_date = cols[1].get_text(" ", strip=True)
        in_time = cols[2].get_text(" ", strip=True) or "-"
        out_time = cols[3].get_text(" ", strip=True) or "-"
        attendance_status = cols[4].get_text(" ", strip=True)
        approval_status = cols[5].get_text(" ", strip=True)

        approve = row.find("a", class_="approve_attendance")
        attendance_id = approve["data-id"] if approve else None

        if attendance_id:
            context.user_data["attendance_ids"][str(i)] = attendance_id

        message += (
            f"{i}. 👤 {staff}\n"
            f"📧 {email}\n"
            f"📅 {attendance_date}\n"
            f"🟢 In : {in_time}\n"
            f"🔴 Out: {out_time}\n"
            f"📌 Attendance : {attendance_status}\n"
            f"✅ Approval : {approval_status}\n\n"
        )

    message += "To approve: /approveattendance number\nExample: /approveattendance 2"

    await update.effective_message.reply_text(message)

async def get_linked_staff(telegram_user_id):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT staff_name, ad_email, ad_password
        FROM staff_accounts
        WHERE telegram_user_id = %s AND is_active = TRUE
    """, (telegram_user_id,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


async def checkin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    staff = await get_linked_staff(
        update.effective_user.id
    )

    if not staff:
        await update.effective_message.reply_text(
            "❌ Staff account not linked. Use /linkstaff first."
        )
        return

    staff_name, ad_email, ad_password = staff

    app_url = os.getenv("ATTENDANCE_APP_URL")

    if not app_url:
        await update.effective_message.reply_text(
            "❌ Attendance App URL is not configured."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📍 Open Attendance App",
                web_app=WebAppInfo(
                    url=app_url
                )
            )
        ]
    ]

    await update.effective_message.reply_text(
        f"🟢 CHECK IN\n\n"
        f"👤 Staff: {staff_name}\n\n"
        f"Open the Attendance App and tap CHECK IN.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def checkout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    staff = await get_linked_staff(
        update.effective_user.id
    )

    if not staff:
        await update.effective_message.reply_text(
            "❌ Staff account not linked. Use /linkstaff first."
        )
        return

    staff_name, ad_email, ad_password = staff

    app_url = os.getenv("ATTENDANCE_APP_URL")

    if not app_url:
        await update.effective_message.reply_text(
            "❌ Attendance App URL is not configured."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📍 Open Attendance App",
                web_app=WebAppInfo(
                    url=app_url
                )
            )
        ]
    ]

    await update.effective_message.reply_text(
        f"🔴 CHECK OUT\n\n"
        f"👤 Staff: {staff_name}\n\n"
        f"Open the Attendance App and tap CHECK OUT.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def receive_attendance_location(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    action = context.user_data.get(
        "attendance_action"
    )

    if not action:
        await update.effective_message.reply_text(
            "❌ No attendance action is pending.\n\n"
            "Use /checkin or /checkout first.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    location = update.effective_message.location

    if not location:
        await update.effective_message.reply_text(
            "❌ Location was not received. "
            "Please try again."
        )
        return

    staff = await get_linked_staff(
        update.effective_user.id
    )

    if not staff:
        context.user_data.pop(
            "attendance_action",
            None
        )

        await update.effective_message.reply_text(
            "❌ Staff account not linked.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    staff_name, ad_email, ad_password = staff

    latitude = location.latitude
    longitude = location.longitude

    try:
        map_link = save_attendance_location(
            staff_name=staff_name,
            telegram_user_id=update.effective_user.id,
            action=action,
            latitude=latitude,
            longitude=longitude
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Location save failed:\n"
            f"{type(e).__name__}: {e}",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    try:
        staff_web = AdvocateWeb(
            email=ad_email.strip(),
            password=ad_password.strip()
        )

        login_ok, login_result = (
            staff_web.test_login()
        )

        if not login_ok:
            await update.effective_message.reply_text(
                f"📍 Location recorded successfully.\n\n"
                f"❌ Advocate Diaries login failed "
                f"for {staff_name}.\n"
                f"Attendance punch was not completed.",
                reply_markup=ReplyKeyboardRemove()
            )

            context.user_data.pop(
                "attendance_action",
                None
            )
            return

        if action == "CHECKIN":
            response = staff_web.punch_in()
            action_text = "Check-in"

        elif action == "CHECKOUT":
            response = staff_web.punch_out()
            action_text = "Check-out"

        else:
            raise Exception(
                f"Unknown attendance action: {action}"
            )

        if response.status_code == 200:
            await update.effective_message.reply_text(
                f"✅ {action_text} completed successfully.\n\n"
                f"👤 Staff: {staff_name}\n"
                f"📍 Location recorded\n"
                f"🗺 Map: {map_link}",
                reply_markup=ReplyKeyboardRemove()
            )

        else:
            await update.effective_message.reply_text(
                f"📍 Location recorded successfully.\n\n"
                f"❌ Advocate Diaries {action_text.lower()} "
                f"failed.\n"
                f"Status: {response.status_code}",
                reply_markup=ReplyKeyboardRemove()
            )

    except Exception as e:
        await update.effective_message.reply_text(
            f"📍 Location recorded successfully.\n\n"
            f"❌ Attendance action failed:\n"
            f"{type(e).__name__}: {e}",
            reply_markup=ReplyKeyboardRemove()
        )

    finally:
        context.user_data.pop(
            "attendance_action",
            None
        )

async def approve_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /approveattendance number")
        return

    key = context.args[0]

    attendance_ids = context.user_data.get("attendance_ids", {})

    attendance_id = attendance_ids.get(key)

    if not attendance_id:
        await update.message.reply_text(
            "Invalid number. First run /attendance DATE, then use /approveattendance number."
        )
        return

    response = web.approve_attendance(attendance_id)

    if response.status_code != 200:
        await update.message.reply_text(
            f"Approval failed. Status: {response.status_code}"
        )
        return

    await update.message.reply_text(
        f"✅ Attendance approved for item {key}.\nRun /attendance DATE to verify."
    )

async def teststafflogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    staff = await get_linked_staff(update.effective_user.id)

    if not staff:
        await update.effective_message.reply_text(
            "❌ Staff account is not linked."
        )
        return

    staff_name, ad_email, ad_password = staff

    try:
        staff_web = AdvocateWeb(
            email=ad_email.strip(),
            password=ad_password.strip()
        )

        ok, result = staff_web.test_login()

        if ok:
            await update.effective_message.reply_text(
                f"✅ Advocate Diaries login successful.\n\n"
                f"👤 Staff: {staff_name}\n"
                f"📧 Email: {ad_email.strip()}\n"
                f"🔗 Final URL: {result}"
            )
        else:
            await update.effective_message.reply_text(
                f"❌ Advocate Diaries login failed.\n\n"
                f"👤 Staff: {staff_name}\n"
                f"📧 Email: {ad_email.strip()}\n"
                f"Reason: {result}"
            )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Login test error.\n\n"
            f"👤 Staff: {staff_name}\n"
            f"📧 Email: {ad_email.strip()}\n"
            f"Error: {type(e).__name__}: {e}"
        )
