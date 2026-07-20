"""Sprint 13.0 Unified Case Workspace UI."""
from __future__ import annotations
import html
from datetime import date
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from services.workspace_v13_service import case_metrics, complete_work, get_case, list_works, recent_cases, search_cases, staff_name_for_user

BUILD = "Sprint 13.0 Unified Case Workspace"

def e(value): return html.escape(str(value if value not in (None, "") else "-"))
def case_key(case): return case.get("case_number") or case.get("case_id") or f"Case {case.get('id')}"

def cases_kb(rows):
    kb=[[InlineKeyboardButton(f"{case_key(c)} · {(c.get('case_title') or '')[:35]}",callback_data=f"s13:case:{c['id']}")] for c in rows]
    kb += [[InlineKeyboardButton("📋 Office Works",callback_data="s13:works:all"),InlineKeyboardButton("👤 My Works",callback_data="s13:works:mine")],[InlineKeyboardButton("🏠 Dashboard",callback_data="s13:dashboard")]]
    return InlineKeyboardMarkup(kb)

def workspace_kb(cid):
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("⚖️ Hearings",callback_data=f"s13:hearings:{cid}"),InlineKeyboardButton("📋 Works",callback_data=f"s13:caseworks:{cid}")],
      [InlineKeyboardButton("📜 Timeline",callback_data=f"s13:timeline:{cid}"),InlineKeyboardButton("📂 Documents",callback_data=f"s13:documents:{cid}")],
      [InlineKeyboardButton("💰 Finance",callback_data=f"s13:finance:{cid}"),InlineKeyboardButton("👤 Client",callback_data=f"s13:client:{cid}")],
      [InlineKeyboardButton("👥 Staff",callback_data=f"s13:staff:{cid}"),InlineKeyboardButton("🔔 Alerts",callback_data=f"s13:alerts:{cid}")],
      [InlineKeyboardButton("🔄 Refresh",callback_data=f"s13:case:{cid}"),InlineKeyboardButton("⬅️ Cases",callback_data="s13:cases")]
    ])

def alerts_for(case,m):
    out=[]
    if m.get('overdue'): out.append(f"🔴 {m['overdue']} overdue Work(s)")
    if m.get('pending'): out.append(f"📋 {m['pending']} pending Work(s)")
    nh=case.get('next_hearing') or case.get('hearing_date')
    if nh: out.append(f"⚖️ Next hearing: {nh}")
    if not m.get('documents'): out.append("📂 No linked documents found")
    if m.get('outstanding',0)>0: out.append(f"💰 Outstanding fee: ₹{m['outstanding']:,.0f}")
    return out[:5]

def render_case(c):
    num=case_key(c); m=case_metrics(int(c['id']),num); alerts=alerts_for(c,m)
    staff=', '.join(m.get('assigned_staff') or []) or 'Unassigned'
    alert_text='\n'.join(f"• {e(x)}" for x in alerts) if alerts else '• No immediate alerts'
    return (
      "🏛 <b>UNIFIED CASE WORKSPACE</b>\n"f"🧩 {BUILD}\n\n"
      f"⚖️ <b>{e(num)}</b>\n📌 {e(c.get('case_title'))}\n👤 {e(c.get('client_name'))} · 📱 {e(c.get('mobile'))}\n\n"
      "<b>COURT STATUS</b>\n"f"📅 Next hearing: <b>{e(c.get('next_hearing') or c.get('hearing_date'))}</b>\n📝 Purpose: {e(c.get('next_purpose'))}\n🏛 Court: {e(c.get('court_name'))}\n👨‍⚖️ Judge: {e(c.get('judge_name'))}\n📍 Case status: <b>{e(c.get('status'))}</b>\n\n"
      "<b>CASE OPERATIONS</b>\n"f"📋 Pending Works: <b>{m['pending']}</b>\n🔴 Overdue Works: <b>{m['overdue']}</b>\n✅ Completed Works: <b>{m['completed']}</b>\n👥 Assigned staff: {e(staff)}\n📜 Timeline entries: {m['timeline']}\n📂 Documents: {m['documents']}\n💰 Outstanding fee: ₹{m['outstanding']:,.0f}\n\n"
      "<b>ALERTS</b>\n"f"{alert_text}")

def work_icon(w):
    if str(w.get('status','')).upper()=='COMPLETED': return '✅'
    if w.get('due_date') and w['due_date']<date.today(): return '🔴'
    return {'URGENT':'🚨','HIGH':'🟠','NORMAL':'🔵','LOW':'⚪'}.get(str(w.get('priority') or 'NORMAL').upper(),'🔵')

def render_works(rows,title):
    lines=[f"📋 <b>{e(title)}</b>",""]
    if not rows: return '\n'.join(lines+["No Works found."])
    for w in rows: lines += [f"{work_icon(w)} <b>Work #{w['id']}</b> · {e(w.get('priority'))}",f"⚖️ {e(w.get('case_number'))} · {e(w.get('case_title'))}",f"📝 {e(w.get('title'))}",f"👤 {e(w.get('assigned_to') or 'Unassigned')} · 📅 {e(w.get('due_date'))}",f"📍 {e(w.get('status'))}",""]
    return '\n'.join(lines)

def works_kb(rows,back='s13:cases',refresh='s13:works:all'):
    kb=[]
    for w in rows:
      if str(w.get('status','')).upper()!='COMPLETED': kb.append([InlineKeyboardButton(f"✅ Complete #{w['id']} · {(w.get('title') or '')[:26]}",callback_data=f"s13:complete:{w['id']}")])
    kb.append([InlineKeyboardButton("🔄 Refresh",callback_data=refresh),InlineKeyboardButton("⬅️ Back",callback_data=back)])
    return InlineKeyboardMarkup(kb)

async def edit(q,text,markup=None):
    try: await q.edit_message_text(text,parse_mode=ParseMode.HTML,reply_markup=markup)
    except BadRequest as exc:
      if 'message is not modified' not in str(exc).lower(): raise

async def caseworkspace13(update:Update,context:ContextTypes.DEFAULT_TYPE):
    term=' '.join(context.args).strip(); rows=search_cases(term,12) if term else recent_cases(10)
    if term and len(rows)==1:
      c=rows[0]; await update.effective_message.reply_text(render_case(c),parse_mode=ParseMode.HTML,reply_markup=workspace_kb(int(c['id']))); return
    await update.effective_message.reply_text(f"🏛 <b>UNIFIED CASE WORKSPACE</b>\n🧩 {BUILD}\n\nSelect a case below or use:\n<code>/caseworkspace CASE_NUMBER</code>",parse_mode=ParseMode.HTML,reply_markup=cases_kb(rows))

async def workboard(update:Update,context:ContextTypes.DEFAULT_TYPE):
    rows=list_works(status='PENDING',limit=25); await update.effective_message.reply_text(render_works(rows,'OFFICE WORK BOARD'),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows))

async def myworks(update:Update,context:ContextTypes.DEFAULT_TYPE):
    staff=staff_name_for_user(update.effective_user.id)
    if not staff: await update.effective_message.reply_text("❌ Your Telegram account is not linked to a staff profile. Use /linkstaff first."); return
    rows=list_works(status='PENDING',assigned_to=staff,limit=25); await update.effective_message.reply_text(render_works(rows,f"{staff.upper()} — MY WORKS"),parse_mode=ParseMode.HTML,reply_markup=works_kb(rows,refresh='s13:works:mine'))

async def workspace13_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); p=(q.data or '').split(':'); action=p[1] if len(p)>1 else 'cases'
    if action=='dashboard': await edit(q,"🏠 Use /morningdashboard to open the Law Office Command Centre."); return
    if action=='cases':
      rows=recent_cases(10); await edit(q,f"🏛 <b>UNIFIED CASE WORKSPACE</b>\n🧩 {BUILD}\n\nSelect a case:",cases_kb(rows)); return
    if action=='case':
      c=get_case(int(p[2])); await edit(q,render_case(c),workspace_kb(int(p[2]))) if c else await edit(q,"❌ Case not found."); return
    if action=='caseworks':
      cid=int(p[2]); c=get_case(cid); rows=list_works(status='ALL',case_id=cid,limit=30); await edit(q,render_works(rows,f"{case_key(c)} — CASE WORKS"),works_kb(rows,f"s13:case:{cid}",f"s13:caseworks:{cid}")); return
    if action=='works':
      mine=len(p)>2 and p[2]=='mine'; staff=staff_name_for_user(q.from_user.id) if mine else None; rows=list_works(status='PENDING',assigned_to=staff,limit=25) if (not mine or staff) else []; title=f"{staff.upper()} — MY WORKS" if staff else 'OFFICE WORK BOARD'; await edit(q,render_works(rows,title),works_kb(rows,refresh=q.data)); return
    if action=='complete':
      r=complete_work(int(p[2]),q.from_user.id)
      if not r: await edit(q,"❌ Work not found."); return
      await edit(q,f"✅ <b>WORK COMPLETED</b>\n\n⚖️ {e(r.get('case_number'))}\n📝 {e(r.get('title'))}\n👤 {e(r.get('assigned_to'))}\n\n✅ Work status updated\n✅ Case timeline updated",InlineKeyboardMarkup([[InlineKeyboardButton("📋 Work Board",callback_data="s13:works:all")],[InlineKeyboardButton("🏛 Open Case",callback_data=f"s13:case:{r.get('case_record_id')}")]])); return
    cid=int(p[2]); c=get_case(cid); num=case_key(c); m=case_metrics(cid,num)
    if action=='timeline': text=f"📜 <b>{e(num)} — TIMELINE</b>\n\nEntries: {m['timeline']}\nUpdated by hearing completions and completed Works."
    elif action=='documents': text=f"📂 <b>{e(num)} — DOCUMENTS</b>\n\nLinked documents: {m['documents']}\nUse <code>/files {e(num)}</code> to browse them."
    elif action=='finance': text=f"💰 <b>{e(num)} — FINANCE</b>\n\nOutstanding fee: ₹{m['outstanding']:,.0f}\nUse <code>/balance {e(num)}</code> for the ledger."
    elif action=='hearings': text=f"⚖️ <b>{e(num)} — HEARINGS</b>\n\nNext hearing: {e(c.get('next_hearing') or c.get('hearing_date'))}\nPurpose: {e(c.get('next_purpose'))}\nUse /livehearings for today's board."
    elif action=='client': text=f"👤 <b>{e(num)} — CLIENT</b>\n\n{e(c.get('client_name'))}\n📱 {e(c.get('mobile'))}"
    elif action=='staff': text=f"👥 <b>{e(num)} — ASSIGNED STAFF</b>\n\n{e(', '.join(m.get('assigned_staff') or []) or 'No staff assigned through Works')}"
    elif action=='alerts': text=f"🔔 <b>{e(num)} — ALERTS</b>\n\n"+('\n'.join(f"• {e(x)}" for x in alerts_for(c,m)) or 'No immediate alerts.')
    else: text='Module ready.'
    await edit(q,text,InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Case Workspace",callback_data=f"s13:case:{cid}")]]))
