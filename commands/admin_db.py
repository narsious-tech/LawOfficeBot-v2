import os
import psycopg2
from telegram import Update
from telegram.ext import ContextTypes

from config import DATABASE_URL

async def refreshofficeprofile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = psycopg2.connect(
        DATABASE_URL
    )
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE office_profile
            SET
                office_name = %s,
                office_whatsapp = %s,
                office_phone = %s,
                office_email = %s,
                court_office_address = %s,
                evening_office_address = %s,
                office_hours = %s,
                website = %s,
                court_maps_link = %s,
                evening_maps_link = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active = TRUE
            RETURNING
                office_name,
                office_whatsapp,
                office_phone,
                office_email,
                court_office_address,
                evening_office_address,
                office_hours
        """, (
            os.getenv(
                "OFFICE_NAME",
                "Law Office of Ajay Chawla"
            ),
            os.getenv(
                "OFFICE_WHATSAPP_NUMBER"
            ),
            os.getenv(
                "OFFICE_PHONE_NUMBER"
            ),
            os.getenv(
                "OFFICE_EMAIL"
            ),
            os.getenv(
                "COURT_OFFICE_ADDRESS",
                "District Courts, Ludhiana"
            ),
            os.getenv(
                "EVENING_OFFICE_ADDRESS"
            ),
            os.getenv(
                "OFFICE_HOURS",
                "Monday-Saturday, 9:30 AM-6:30 PM"
            ),
            os.getenv(
                "OFFICE_WEBSITE"
            ),
            os.getenv(
                "COURT_OFFICE_MAPS_LINK"
            ),
            os.getenv(
                "EVENING_OFFICE_MAPS_LINK"
            ),
        ))

        row = cur.fetchone()

        if not row:
            await update.effective_message.reply_text(
                "❌ No active office profile was found."
            )
            return

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Office profile refresh failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    (
        office_name,
        office_whatsapp,
        office_phone,
        office_email,
        court_address,
        evening_address,
        office_hours
    ) = row

    await update.effective_message.reply_text(
        "✅ OFFICE PROFILE REFRESHED\n\n"
        f"🏢 Office: {office_name or '-'}\n"
        f"📱 WhatsApp: {office_whatsapp or '-'}\n"
        f"☎️ Phone: {office_phone or '-'}\n"
        f"✉️ Email: {office_email or '-'}\n"
        f"📍 Court Office: {court_address or '-'}\n"
        f"📍 Evening Office: {evening_address or '-'}\n"
        f"🕒 Hours: {office_hours or '-'}"
    )

async def show_case_columns(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'cases'
            ORDER BY ordinal_position
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "❌ No columns found for table: cases"
        )
        return

    message = (
        "📋 CASES TABLE COLUMNS\n\n"
        + "\n".join(
            f"{index}. {row[0]}"
            for index, row in enumerate(
                rows,
                start=1
            )
        )
    )

    await update.effective_message.reply_text(
        message
    )

async def show_table_columns(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/tablecolumns TABLE_NAME\n\n"
            "Examples:\n"
            "/tablecolumns cases\n"
            "/tablecolumns client_contacts\n"
            "/tablecolumns client_messages"
        )
        return

    table_name = context.args[0].strip().lower()

    allowed_tables = {
        "cases",
        "client_contacts",
        "client_messages",
    }

    if table_name not in allowed_tables:
        await update.effective_message.reply_text(
            "❌ Table not allowed."
        )
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                column_name,
                data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (
            table_name,
        ))

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"❌ No columns found for table: {table_name}"
        )
        return

    message = (
        f"📋 TABLE: {table_name}\n\n"
        + "\n".join(
            f"{index}. {column_name} — {data_type}"
            for index, (
                column_name,
                data_type
            ) in enumerate(
                rows,
                start=1
            )
        )
    )

    await update.effective_message.reply_text(
        message
    )
