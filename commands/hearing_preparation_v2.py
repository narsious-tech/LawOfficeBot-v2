from __future__ import annotations
import asyncio
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import ContextTypes
from services.hearing_preparation_v2_service import preparation_rows,update_step,summary

IST=ZoneInfo("Asia/Kolkata")
PAGE=6

def _target():
    return datetime.now(IST).date()+timedelta(days=1)

def _mark(v):
    return {"READY":"✅","BROUGHT":"✅","NOT_REQUIRED":"➖","ATTENTION":"⚠️","NEEDS_ATTENTION":"⚠️","NOT_FOUND":"❌","PENDING":"⬜"}.get(v,"⬜")

def _row_text(r):
    return "\n".join([
      f"⚖️ {r['case_number']}",
      str(r.get("case_title") or ""),
      f"{r.get('court') or '-'} | Floor {r.get('floor') or '-'} | Room {r.get('room') or '-'}",
      f"Purpose: {r.get('purpose') or '-'}",
      "",
      f"{_mark(r['physical_file_status'])} Physical file: {r['physical_file_status'].replace('_',' ')}",
      f"{_mark(r['documents_status'])} Documents: {r['documents_status'].replace('_',' ')}",
      f"{_mark(r['previous_order_status'])} Previous order: {r['previous_order_status'].replace('_',' ')}",
      f"{_mark(r['instructions_status'])} Instructions: {r['instructions_status'].replace('_',' ')}",
      f"Preparation: {_mark(r['overall_status'])} {r['overall_status'].replace('_',' ')}",
    ])

def _keyboard(r):
    i=r["id"]
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("📁 File brought",callback_data=f"hp2:{i}:FILE:BROUGHT"),
       InlineKeyboardButton("❌ File missing",callback_data=f"hp2:{i}:FILE:NOT_FOUND")],
      [InlineKeyboardButton("📄 Documents checked",callback_data=f"hp2:{i}:DOCUMENTS:READY"),
       InlineKeyboardButton("⚠️ Docs attention",callback_data=f"hp2:{i}:DOCUMENTS:ATTENTION")],
      [InlineKeyboardButton("📜 Order checked",callback_data=f"hp2:{i}:ORDER:READY"),
       InlineKeyboardButton("➖ No order needed",callback_data=f"hp2:{i}:ORDER:NOT_REQUIRED")],
      [InlineKeyboardButton("👤 Instructions ready",callback_data=f"hp2:{i}:INSTRUCTIONS:READY"),
       InlineKeyboardButton("➖ No instructions",callback_data=f"hp2:{i}:INSTRUCTIONS:NOT_REQUIRED")],
      [InlineKeyboardButton("⚠️ File attention",callback_data=f"hp2:{i}:FILE:NEEDS_ATTENTION")],
    ])

async def preparation(update:Update,context:ContextTypes.DEFAULT_TYPE):
    target=_target()
    rows=await asyncio.to_thread(preparation_rows,target)
    s=await asyncio.to_thread(summary,target)
    await update.effective_message.reply_text(
      "🗂 COURT PREPARATION CONTROL\n"
      f"📅 {target.strftime('%d %b %Y')}\n\n"
      f"Matters: {s['total']} | Ready: {s['ready']} | Not ready: {s['not_ready']} | Attention: {s['attention']}\n"
      f"Files required: {s['files_required']} | Brought: {s['files_brought']} | Missing: {s['files_missing']}\n\n"
      "Update each matter with the buttons below."
    )
    if not rows:
        await update.effective_message.reply_text("No selected physical-file matters are available for tomorrow. First select/send files from /eveningdashboard.")
        return
    for r in rows:
        await update.effective_message.reply_text(_row_text(r),reply_markup=_keyboard(r))

async def preparationstatus(update:Update,context:ContextTypes.DEFAULT_TYPE):
    target=_target()
    s=await asyncio.to_thread(summary,target)
    await update.effective_message.reply_text(
      "📊 HEARING PREPARATION STATUS\n"
      f"📅 {target.strftime('%d %b %Y')}\n\n"
      f"⚖️ Matters tracked: {s['total']}\n"
      f"✅ Ready: {s['ready']}\n"
      f"⬜ Not ready: {s['not_ready']}\n"
      f"⚠️ Attention: {s['attention']}\n\n"
      f"📁 Files required: {s['files_required']}\n"
      f"✅ Files brought: {s['files_brought']}\n"
      f"❌ Files missing: {s['files_missing']}"
    )

async def hearing_preparation_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    try:
        _,sid,step,status=q.data.split(":",3)
        r=await asyncio.to_thread(update_step,int(sid),step,status,q.from_user.id,q.from_user.full_name)
    except Exception:
        await q.edit_message_text("⚠️ Could not update hearing preparation. Please reopen /preparation.")
        return
    if not r:
        await q.edit_message_text("⚠️ Preparation record no longer exists.")
        return
    await q.edit_message_text(_row_text(r),reply_markup=_keyboard(r))
