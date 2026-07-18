"""
Exact production extraction of `/findcase`.
"""

import psycopg2
from telegram import Update
from telegram.ext import ContextTypes

from config import DATABASE_URL

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

async def findcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Use: /findcase CASEID\n"
            "Example: /findcase CLA-2026-9500"
        )
        return

    case_id = context.args[0].strip()

    cur.execute("""
        SELECT
            case_id,
            client_name,
            mobile,
            case_type,
            court_name,
            opposite_party,
            hearing_date,
            drive_folder_link,
            ad_sync_status,
            ad_created_at,
            ad_sync_message
        FROM cases
        WHERE case_id = %s
    """, (case_id,))

    result = cur.fetchone()

    if not result:
        await update.message.reply_text(
            f"❌ Case not found: {case_id}"
        )
        return

    folder_link = result[7] or "Not created"

    await update.message.reply_text(
        f"📁 CASE FOUND\n\n"
        f"🆔 Case ID: {result[0]}\n"
        f"👤 Client: {result[1]}\n"
        f"📱 Mobile: {result[2]}\n"
        f"⚖️ Type: {result[3]}\n"
        f"🏛 Court: {result[4]}\n"
        f"👥 Opposite: {result[5]}\n"
        f"📅 Hearing: {result[6]}\n\n"
        f"📂 Google Drive Folder:\n"
        f"{folder_link}"
        f"\n📘 Advocate Diaries Sync: {result[8] or 'Not recorded'}\n"
        f"🕒 AD Created At: {result[9] or '-'}\n"
        f"📝 Sync Message:\n{result[10] or '-'}"
    )
