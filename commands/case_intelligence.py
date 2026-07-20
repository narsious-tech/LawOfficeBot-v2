"""Sprint 15 commands and scheduled next-date dispatch."""
from __future__ import annotations
import os
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from services.case_intelligence_service import todays_next_dates, render_next_dates, jimmy_telegram_id

async def nextdateslist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(render_next_dates(todays_next_dates()), parse_mode=ParseMode.HTML)

async def physical_file_next_dates_job(context: ContextTypes.DEFAULT_TYPE):
    text=render_next_dates(todays_next_dates())
    recipients=[]
    admin=os.getenv('ADMIN_CHAT_ID') or os.getenv('TELEGRAM_ADMIN_CHAT_ID')
    if admin:
        recipients.append(int(admin))
    jimmy=jimmy_telegram_id()
    if jimmy and jimmy not in recipients:
        recipients.append(jimmy)
    for chat_id in recipients:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            print(f"Sprint 15 next-date dispatch failed for {chat_id}: {exc}")
