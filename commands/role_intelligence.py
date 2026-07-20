from __future__ import annotations
import asyncio, os
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import ContextTypes
from services.role_intelligence_service import role_dashboard,file_assignments,update_file_status
from services.case_intelligence_service import staff_telegram_id
IST=ZoneInfo('Asia/Kolkata')

def _render(d):
 p=d['profile']; f=d['files']; w=d['works']; name=p['staff_name']; role=p['role']
 executive=role in ('admin','owner','principal','supervisor','manager') or name.lower() in ('ajay','priya')
 title='🏢 OFFICE STATUS' if executive else '👤 MY DASHBOARD'
 return '\n'.join([title,f"Staff: {name}",f"Role: {role.title()}",'',
 '📁 PHYSICAL FILES',f"Selected/Pending: {f.get('pending',0)}",f"Brought: {f.get('brought',0)}",f"Not found: {f.get('not_found',0)}",f"Needs attention: {f.get('attention',0)}",'',
 '📝 WORKS',f"Pending: {w.get('pending',0)}",f"Due today: {w.get('due_today',0)}",f"Overdue: {w.get('overdue',0)}",'',f"⚠️ Pending hearing updates: {d.get('pending_updates',0)}"])

async def mydashboard(update:Update,context:ContextTypes.DEFAULT_TYPE):
 d=await asyncio.to_thread(role_dashboard,update.effective_user.id)
 await update.effective_message.reply_text(_render(d))

async def officestatus(update:Update,context:ContextTypes.DEFAULT_TYPE):
 d=await asyncio.to_thread(role_dashboard,update.effective_user.id)
 name=d['profile']['staff_name'].lower(); role=d['profile']['role']
 if role not in ('admin','owner','principal','supervisor','manager') and name not in ('ajay','priya'):
  await update.effective_message.reply_text('⛔ Office-wide status is available only to Ajay and Priya. Use /mydashboard.')
  return
 await update.effective_message.reply_text(_render(d))

def file_status_keyboard(row):
 i=row['id']
 return InlineKeyboardMarkup([[InlineKeyboardButton('✅ Brought',callback_data=f'pfs:{i}:BROUGHT'),InlineKeyboardButton('❌ Not found',callback_data=f'pfs:{i}:NOT_FOUND')],[InlineKeyboardButton('⚠️ Needs attention',callback_data=f'pfs:{i}:NEEDS_ATTENTION')]])

async def myfilesstatus(update:Update,context:ContextTypes.DEFAULT_TYPE):
 target=datetime.now(IST).date()+timedelta(days=1)
 rows=await asyncio.to_thread(file_assignments,target)
 if not rows:
  await update.effective_message.reply_text('No physical files have been selected for tomorrow.')
  return
 for r in rows:
  text=f"📁 {r['case_number']}\n{r.get('case_title') or ''}\nStatus: {r['status']}\nCourt: {r.get('court') or '-'} | Floor {r.get('floor') or '-'} | Room {r.get('room') or '-'}"
  await update.effective_message.reply_text(text,reply_markup=file_status_keyboard(r))

async def physical_file_status_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
 q=update.callback_query; await q.answer()
 try:
  _,sid,status=q.data.split(':',2); row=await asyncio.to_thread(update_file_status,int(sid),status,q.from_user.id,q.from_user.full_name)
 except Exception:
  await q.answer('Could not update file status.',show_alert=True); return
 if not row:
  await q.answer('File assignment not found.',show_alert=True); return
 await q.edit_message_text(f"📁 {row['case_number']}\n{row.get('case_title') or ''}\nStatus: {row['status']}\nUpdated by: {row.get('status_by_name') or q.from_user.full_name}",reply_markup=file_status_keyboard(row))
 if status in ('NOT_FOUND','NEEDS_ATTENTION'):
  alert=f"🚨 PHYSICAL FILE EXCEPTION\n{row['case_number']}\n{row.get('case_title') or ''}\nStatus: {status.replace('_',' ')}\nReported by: {q.from_user.full_name}"
  for name in ('Ajay','Priya'):
   tid=await asyncio.to_thread(staff_telegram_id,name)
   if tid:
    try: await context.bot.send_message(tid,alert)
    except Exception: pass
