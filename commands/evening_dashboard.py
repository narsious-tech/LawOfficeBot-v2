from __future__ import annotations
import os, tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from commands.dashboard import fetch_advocate_diaries_cause_groups
from services.printable_causelist_service import build_causelist_pdf


def _blackouts(groups):
    vals=[]
    for g in groups:
        for key in ('blackout_dates','blackouts'):
            v=g.get(key)
            if isinstance(v,list): vals.extend(str(x) for x in v if x)
    return list(dict.fromkeys(vals))

async def _pdf_for(target_date):
    import asyncio
    groups, source = await asyncio.to_thread(fetch_advocate_diaries_cause_groups, target_date)
    out=os.path.join(tempfile.gettempdir(), f"Cause_List_{target_date.isoformat()}.pdf")
    await asyncio.to_thread(build_causelist_pdf, groups, target_date, _blackouts(groups), out)
    return groups, source, out

async def printablecauselist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now=datetime.now(ZoneInfo('Asia/Kolkata'))
    target=now.date()
    if context.args and context.args[0].lower() in ('tomorrow','tom'):
        target += timedelta(days=1)
    groups, source, path = await _pdf_for(target)
    with open(path,'rb') as fh:
        await update.effective_message.reply_document(fh, filename=os.path.basename(path), caption=f"Printable cause list - {target.strftime('%d %b %Y')} - Advocate Diaries {source}")

async def eveningdashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_dashboard(context, chat_id=update.effective_chat.id, force=True)

async def send_evening_dashboard(context, chat_id=None, force=False):
    now=datetime.now(ZoneInfo('Asia/Kolkata')); target=now.date()+timedelta(days=1)
    groups, source, path = await _pdf_for(target)
    total=sum(len(g.get('cases') or []) for g in groups)
    text=(f"🌆 EVENING OPERATIONS DASHBOARD\n📅 Tomorrow: {target.strftime('%d %b %Y')}\n⚖ Matters: {total}\n"
          "📁 Mark required physical files from tomorrow's cause list.\n"
          "📋 Preparation priority: urgent, overdue, due today and due tomorrow Works.\n\n"
          "Printable Legal-size cause list attached.")
    dest=chat_id or int(os.getenv('OFFICE_GROUP_CHAT_ID') or os.getenv('PHYSICAL_FILE_GROUP_CHAT_ID'))
    await context.bot.send_message(chat_id=dest,text=text)
    with open(path,'rb') as fh:
        await context.bot.send_document(chat_id=dest,document=fh,filename=os.path.basename(path))

async def evening_dashboard_job(context):
    await send_evening_dashboard(context)
