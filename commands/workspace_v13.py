"""Sprint 13 Case Workspace and Work Management UI."""
from __future__ import annotations
import html
from datetime import date
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from services.workspace_v13_service import recent_cases, search_cases, get_case, case_metrics, list_works, staff_name_for_user, complete_work


def e(v): return html.escape(str(v or '-'))

def case_key(c): return c.get('case_number') or c.get('case_id') or f"Case {c.get('id')}"

def cases_kb(rows):
    kb=[[InlineKeyboardButton(f"{case_key(c)} · {(c.get('case_title') or '')[:35]}", callback_data=f"s13:case:{c['id']}")] for c in rows]
    kb.append([InlineKeyboardButton("📋 Work Board", callback_data="s13:works:all"),InlineKeyboardButton("👤 My Works", callback_data="s13:works:mine")])
    return InlineKeyboardMarkup(kb)

def workspace_kb(cid):
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("📋 Pending Works",callback_data=f"s13:caseworks:{cid}"),InlineKeyboardButton("📜 Timeline",callback_data=f"s13:timeline:{cid}")],
      [InlineKeyboardButton("📂 Documents",callback_data=f"s13:documents:{cid}"),InlineKeyboardButton("💰 Finance",callback_data=f"s13:finance:{cid}")],
      [InlineKeyboardButton("⚖️ Hearings",callback_data=f"s13:hearings:{cid}"),InlineKeyboardButton("👤 Client",callback_data=f"s13:client:{cid}")],
      [InlineKeyboardButton("🔄 Refresh",callback_data=f"s13:case:{cid}"),InlineKeyboardButton("⬅️ Cases",callback_data="s13:cases")]
    ])

def render_case(c):
    num=case_key(c); m=case_metrics(int(c['id']),num)
    agreed=float(c.get('fee_agreed') or 0); advance=float(c.get('advance_received') or 0)
    outstanding=max(0,agreed-advance)
    return ("🏛 <b>CASE WORKSPACE</b>\n\n"
      f"⚖️ <b>{e(num)}</b>\n📌 {e(c.get('case_title'))}\n👤 {e(c.get('client_name'))} · {e(c.get('mobile'))}\n\n"
      f"📅 Next hearing: <b>{e(c.get('next_hearing') or c.get('hearing_date'))}</b>\n"
      f"📝 Purpose: {e(c.get('next_purpose'))}\n🏛 {e(c.get('court_name'))}\n👨‍⚖️ {e(c.get('judge_name'))}\n\n"
      f"📋 Works: <b>{m['pending']}</b> pending · {m['overdue']} overdue · {m['completed']} completed\n"
      f"📜 Timeline entries: {m['timeline']}\n📂 Documents: {m['documents']}\n"
      f"💰 Fee outstanding: ₹{outstanding:,.0f}\n📍 Status: <b>{e(c.get('status'))}</b>")

def work_icon(w):
    if str(w.get('status','')).upper()=='COMPLETED': return '✅'
    if w.get('due_date') and w['due_date'] < date.today(): return '🔴'
    return {'URGENT':'🚨','HIGH':'🟠','NORMAL':'🔵','LOW':'⚪'}.get(str(w.get('priority') or 'NORMAL').upper(),'🔵')

def render_works(rows,title):
    lines=[f"📋 <b>{e(title)}</b>",""]
    if not rows: return "\n".join(lines+["No works found."])
    for w in rows:
      lines += [f"{work_icon(w)} <b>Work #{w['id']}</b> · {e(w.get('priority'))}",f"⚖️ {e(w.get('case_number'))} · {e(w.get('case_title'))}",f"📝 {e(w.get('title'))}",f"👤 {e(w.get('assigned_to') or 'Unassigned')} · 📅 {e(w.get('due_date'))}",""]
    return "\n".join(lines)

def works_kb(rows,back="s13:cases"):
    kb=[]
    for w in rows:
      if str(w.get('status','')).upper()!='COMPLETED': kb.append([InlineKeyboardButton(f"✅ Complete #{w['id']} · {(w.get('title') or '')[:28]}",callback_data=f"s13:complete:{w['id']}")])
    kb.append([InlineKeyboardButton("🔄 Refresh",callback_data="s13:works:all"),InlineKeyboardButton("⬅️ Back",callback_data=back)])
    return InlineKeyboardMarkup(kb)

async def caseworkspace13(update:Update,context:ContextTypes.DEFAULT_TYPE):
    term=' '.join(context.args).strip(); rows=search_cases(term,12) if term else recent_cases(10)
    await update.effective_message.reply_text("🏛 <b>SPRINT 13 CASE WORKSPACE</b>\nSelect a case or use <code>/caseworkspace CASE_NUMBER</code>.",parse_mode=ParseMode.HTML,reply_markup=cases_kb(rows))

async def workboard(update:Update,context:ContextTypes.DEFAULT_TYPE):
    rows=list_works(status='PENDING',limit=25)
    await update.effective_message.reply_text(render_works(rows,"OFFICE WORK BOARD"),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows))

async def myworks(update:Update,context:ContextTypes.DEFAULT_TYPE):
    staff=staff_name_for_user(update.effective_user.id)
    if not staff:
      await update.effective_message.reply_text("❌ Your Telegram account is not linked to a staff profile. Use /linkstaff first."); return
    rows=list_works(status='PENDING',assigned_to=staff,limit=25)
    await update.effective_message.reply_text(render_works(rows,f"{staff.upper()} — MY WORKS"),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows))

async def workspace13_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); p=(q.data or '').split(':'); action=p[1] if len(p)>1 else 'cases'
    if action=='cases':
      rows=recent_cases(10); await q.edit_message_text("🏛 <b>SPRINT 13 CASE WORKSPACE</b>\nSelect a case:",parse_mode=ParseMode.HTML,reply_markup=cases_kb(rows)); return
    if action=='case':
      c=get_case(int(p[2]));
      if not c: await q.edit_message_text("❌ Case not found."); return
      await q.edit_message_text(render_case(c),parse_mode=ParseMode.HTML,reply_markup=workspace_kb(int(p[2]))); return
    if action=='caseworks':
      cid=int(p[2]); c=get_case(cid); rows=list_works(status='ALL',case_id=cid,limit=30)
      await q.edit_message_text(render_works(rows,f"{case_key(c)} — WORKS"),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows,f"s13:case:{cid}")); return
    if action=='works':
      mine=len(p)>2 and p[2]=='mine'; staff=staff_name_for_user(q.from_user.id) if mine else None
      rows=list_works(status='PENDING',assigned_to=staff,limit=25) if (not mine or staff) else []
      await q.edit_message_text(render_works(rows,(f"{staff.upper()} — MY WORKS" if staff else "OFFICE WORK BOARD")),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows)); return
    if action=='complete':
      wid=int(p[2]); result=complete_work(wid,q.from_user.id)
      if not result: await q.edit_message_text("❌ Work not found."); return
      rows=list_works(status='PENDING',limit=25)
      await q.edit_message_text(f"✅ <b>WORK COMPLETED</b>\n\n⚖️ {e(result.get('case_number'))}\n📝 {e(result.get('title'))}\n👤 {e(result.get('assigned_to'))}\n\nThe case timeline was updated.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Back to Work Board",callback_data="s13:works:all")],[InlineKeyboardButton("🏛 Open Case",callback_data=f"s13:case:{result.get('case_record_id')}")]])); return
    cid=int(p[2]); c=get_case(cid)
    labels={'timeline':'📜 Timeline is available in the case history and is updated by hearings and completed works.','documents':'📂 Use /files '+case_key(c)+' to browse case documents.','finance':'💰 Use /balance '+case_key(c)+' to view the case balance.','hearings':'⚖️ Use /livehearings for today’s live hearing board.','client':'👤 Client: '+e(c.get('client_name'))+'\n📱 '+e(c.get('mobile'))}
    await q.edit_message_text(labels.get(action,'Module ready.'),parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Case Workspace",callback_data=f"s13:case:{cid}")]]))
