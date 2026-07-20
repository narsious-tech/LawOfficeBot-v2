"""Sprint 13.1 Case Workspace dashboard UI."""
from __future__ import annotations
import html
from datetime import date, datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from services.case_assignment_service import get_case_owner
from services.workspace_v13_service import (
    case_metrics, complete_work, get_case, list_works, recent_cases, search_cases,
    staff_name_for_user, timeline_entries,
)

BUILD = "Sprint 14.0.2 Workspace Responsibility Polish"

def e(v): return html.escape(str(v if v not in (None, "") else "-"))
def case_key(c): return c.get("case_number") or c.get("case_id") or f"Case {c.get('id')}"
def fmt_date(v):
    if not v: return "Not fixed"
    if isinstance(v, (date, datetime)): return v.strftime("%d %b %Y")
    s=str(v)
    try: return datetime.fromisoformat(s[:10]).strftime("%d %b %Y")
    except Exception: return s

def cases_kb(rows):
    kb=[[InlineKeyboardButton(f"{case_key(c)} · {(c.get('case_title') or '')[:34]}",callback_data=f"s13:case:{c['id']}")] for c in rows]
    kb += [[InlineKeyboardButton("📋 Office Works",callback_data="s13:works:all"),InlineKeyboardButton("👤 My Works",callback_data="s13:works:mine")],[InlineKeyboardButton("🏠 Dashboard",callback_data="s13:dashboard")]]
    return InlineKeyboardMarkup(kb)

def workspace_kb(cid):
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("⚖️ Hearings",callback_data=f"s13:hearings:{cid}"),InlineKeyboardButton("📋 Works",callback_data=f"s13:caseworks:{cid}")],
      [InlineKeyboardButton("📜 Timeline",callback_data=f"s13:timeline:{cid}"),InlineKeyboardButton("📂 Documents",callback_data=f"s13:documents:{cid}")],
      [InlineKeyboardButton("💰 Finance",callback_data=f"s13:finance:{cid}"),InlineKeyboardButton("👤 Client",callback_data=f"s13:client:{cid}")],
      [InlineKeyboardButton("👥 Case Team",callback_data=f"s13:staff:{cid}"),InlineKeyboardButton("🩺 Case Health",callback_data=f"s13:health:{cid}")],
      [InlineKeyboardButton("🔄 Refresh",callback_data=f"s13:case:{cid}"),InlineKeyboardButton("⬅️ Cases",callback_data="s13:cases")]
    ])

def health_lines(c,m):
    checks=[]
    checks.append((bool(c.get('next_hearing') or c.get('hearing_date')),"Next hearing recorded"))
    checks.append((bool(c.get('ad_sync_status') or c.get('advocate_diaries_case_id') or c.get('ad_case_id')),"Advocate Diaries linked"))
    checks.append((m.get('timeline',0)>0,"Timeline available"))
    checks.append((bool(c.get('drive_folder_link')),"Drive folder linked"))
    checks.append((bool(c.get('client_name')),"Client assigned"))
    checks.append((True,"Case ownership assigned"))
    checks.append((True,"Work supervision assigned to Priya"))
    return [("✅" if ok else "⚠️",label) for ok,label in checks]

def render_case(c):
    num=case_key(c); m=case_metrics(int(c['id']),num)
    try:
        ownership=get_case_owner(num,c.get('court_floor') or c.get('floor'),case_record_id=int(c['id']),court=c.get('court_name'),judge=c.get('judge_name'))
        case_owner=ownership.get('owner_staff') or 'Preet'
        owner_reason=f"Floor {ownership.get('source_floor')}" if ownership.get('source_floor') is not None else 'Missing/invalid floor fallback'
    except Exception:
        case_owner='Preet'; owner_reason='Default fallback'
    work_assignees=', '.join(m.get('assigned_staff') or [])
    work_assignment_line = f"👥 Active Work assignees: {e(work_assignees)}" if work_assignees else "📋 Active Works: None"
    next_hearing=fmt_date(c.get('next_hearing') or c.get('hearing_date'))
    status=str(c.get('status') or 'OPEN').upper()
    status_icon='🟢' if status in {'OPEN','ACTIVE'} else '⚪'
    alerts=[]
    if m['overdue']: alerts.append(f"🔴 {m['overdue']} overdue Work(s)")
    if m['pending']: alerts.append(f"🟠 {m['pending']} pending Work(s)")
    if not c.get('drive_folder_link'): alerts.append("📂 Drive folder missing")
    alert_text='\n'.join(f"• {e(x)}" for x in alerts[:4]) if alerts else '• No immediate operational alerts'
    return (
      "⚖️ <b>CASE WORKSPACE</b>\n"f"🧩 {BUILD}\n\n"
      f"<b>{e(num)}</b>\n📌 {e(c.get('case_title'))}\n👤 {e(c.get('client_name'))} · 📱 {e(c.get('mobile'))}\n\n"
      f"{status_icon} <b>Status: {e(status)}</b>\n\n"
      "<b>COURT</b>\n"
      f"🏛 {e(c.get('court_name'))}\n👨‍⚖️ {e(c.get('judge_name'))}\n📅 <b>{e(next_hearing)}</b>\n📍 {e(c.get('next_purpose'))}\n\n"
      "<b>CASE OVERVIEW</b>\n"
      f"📋 Works: <b>{m['pending']}</b> pending · {m['completed']} completed\n"
      f"📂 Documents: <b>{m['documents']}</b>\n"
      "<b>OFFICE RESPONSIBILITY</b>\n"
      f"👤 Case Owner: <b>{e(case_owner)}</b>\n"
      f"👩‍💼 Work Supervisor: <b>Priya</b>\n"
      f"🏢 Assignment Basis: {e(owner_reason)}\n"
      f"🤖 Assignment Mode: Automatic\n"
      f"{work_assignment_line}\n\n"
      f"📜 Timeline: <b>{m['timeline']}</b> entries\n"
      f"💰 Outstanding: <b>₹{m['outstanding']:,.0f}</b>\n\n"
      "<b>ATTENTION</b>\n"+alert_text
    )

def work_icon(w):
    if str(w.get('status','')).upper()=='COMPLETED': return '✅'
    if w.get('due_date') and w['due_date']<date.today(): return '🔴'
    return {'URGENT':'🚨','HIGH':'🟠','NORMAL':'🔵','LOW':'⚪'}.get(str(w.get('priority') or 'NORMAL').upper(),'🔵')

def render_works(rows,title):
    lines=[f"📋 <b>{e(title)}</b>",""]
    if not rows: return '\n'.join(lines+["No Works found.","","Use hearing completion to create assigned Works."])
    for w in rows:
        due=fmt_date(w.get('due_date'))
        lines += [f"{work_icon(w)} <b>{e(w.get('title'))}</b>",f"⚖️ {e(w.get('case_number'))} · {e(w.get('case_title'))}",f"👤 {e(w.get('assigned_to') or 'Unassigned')} · 📅 {e(due)}",f"🚦 {e(w.get('priority'))} · 📍 {e(w.get('status'))}",""]
    return '\n'.join(lines)

def works_kb(rows,back='s13:cases',refresh='s13:works:all'):
    kb=[]
    for w in rows:
      if str(w.get('status','')).upper()!='COMPLETED': kb.append([InlineKeyboardButton(f"✅ Complete · {(w.get('title') or '')[:30]}",callback_data=f"s13:complete:{w['id']}")])
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
    await update.effective_message.reply_text(f"⚖️ <b>CASE WORKSPACE</b>\n🧩 {BUILD}\n\nSelect a case below or use:\n<code>/caseworkspace CASE_NUMBER</code>",parse_mode=ParseMode.HTML,reply_markup=cases_kb(rows))

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
      rows=recent_cases(10); await edit(q,f"⚖️ <b>CASE WORKSPACE</b>\n🧩 {BUILD}\n\nSelect a case:",cases_kb(rows)); return
    if action=='case':
      c=get_case(int(p[2])); await edit(q,render_case(c),workspace_kb(int(p[2]))) if c else await edit(q,"❌ Case not found."); return
    if action=='caseworks':
      cid=int(p[2]); c=get_case(cid); rows=list_works(status='ALL',case_id=cid,limit=30); await edit(q,render_works(rows,f"{case_key(c)} — CASE WORKS"),works_kb(rows,f"s13:case:{cid}",f"s13:caseworks:{cid}")); return
    if action=='works':
      mine=len(p)>2 and p[2]=='mine'; staff=staff_name_for_user(q.from_user.id) if mine else None; rows=list_works(status='PENDING',assigned_to=staff,limit=25) if (not mine or staff) else []; title=f"{staff.upper()} — MY WORKS" if staff else 'OFFICE WORK BOARD'; await edit(q,render_works(rows,title),works_kb(rows,refresh=q.data)); return
    if action=='complete':
      r=complete_work(int(p[2]),q.from_user.id)
      if not r: await edit(q,"❌ Work not found."); return
      await edit(q,f"✅ <b>WORK COMPLETED</b>\n\n⚖️ {e(r.get('case_number'))}\n📝 {e(r.get('title'))}\n👤 {e(r.get('assigned_to'))}\n\n✅ Work status updated\n✅ Case timeline updated",InlineKeyboardMarkup([[InlineKeyboardButton("📋 Work Board",callback_data="s13:works:all")],[InlineKeyboardButton("⚖️ Open Case",callback_data=f"s13:case:{r.get('case_record_id')}")]])); return
    cid=int(p[2]); c=get_case(cid); num=case_key(c); m=case_metrics(cid,num)
    if action=='timeline':
      rows=timeline_entries(cid,num,12); lines=[f"📜 <b>{e(num)} — CASE TIMELINE</b>",""]
      if not rows: lines.append("No timeline entries found.")
      for r in rows:
        lines += [f"📅 <b>{e(fmt_date(r.get('event_date') or r.get('created_at')))}</b>",f"{e(r.get('event_type'))} · {e(r.get('status'))}",f"{e(r.get('outcome') or r.get('preparation'))}",""]
      text='\n'.join(lines)
    elif action=='documents': text=f"📂 <b>{e(num)} — DOCUMENTS</b>\n\nLinked documents: <b>{m['documents']}</b>\n\nUse <code>/files {e(num)}</code> to browse or upload documents."
    elif action=='finance': text=f"💰 <b>{e(num)} — FEES</b>\n\nOutstanding balance: <b>₹{m['outstanding']:,.0f}</b>\n\nUse <code>/balance {e(num)}</code> for the complete ledger."
    elif action=='hearings': text=f"⚖️ <b>{e(num)} — HEARINGS</b>\n\nNext hearing: <b>{e(fmt_date(c.get('next_hearing') or c.get('hearing_date')))}</b>\nPurpose: {e(c.get('next_purpose'))}\nCourt: {e(c.get('court_name'))}\nJudge: {e(c.get('judge_name'))}"
    elif action=='client': text=f"👤 <b>{e(num)} — CLIENT</b>\n\n<b>{e(c.get('client_name'))}</b>\n📱 {e(c.get('mobile'))}"
    elif action=='staff':
      ownership=get_case_owner(num,c.get('court_floor') or c.get('floor'),case_record_id=cid,court=c.get('court_name'),judge=c.get('judge_name'))
      reason=f"Court floor {ownership.get('source_floor')}" if ownership.get('source_floor') is not None else 'Missing/invalid floor → Preet'
      active_assignees=', '.join(m.get('assigned_staff') or [])
      work_summary=f"{m.get('pending',0)} pending · {m.get('completed',0)} completed"
      assignee_text=active_assignees if active_assignees else 'No active Works'
      text=(f"👥 <b>{e(num)} — CASE TEAM</b>\n\n"
            f"<b>CASE OWNERSHIP</b>\n"
            f"👤 Owner: <b>{e(ownership.get('owner_staff') or 'Preet')}</b>\n"
            f"⚙️ Mode: {e(ownership.get('assignment_mode') or 'AUTO_FLOOR')}\n"
            f"📍 Basis: {e(reason)}\n\n"
            f"<b>WORK SUPERVISION</b>\n"
            f"👩‍💼 Supervisor: <b>Priya</b>\n"
            f"📋 Works: {e(work_summary)}\n"
            f"👥 Active assignees: {e(assignee_text)}")
    elif action=='health':
      lines=[f"🩺 <b>{e(num)} — CASE HEALTH</b>",""]+[f"{icon} {e(label)}" for icon,label in health_lines(c,m)]
      text='\n'.join(lines)
    else: text='Module ready.'
    await edit(q,text,InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Case Workspace",callback_data=f"s13:case:{cid}")]]))
