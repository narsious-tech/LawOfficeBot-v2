from __future__ import annotations
import html
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from services.case_assignment_service import supervision_summary, reconcile_live_hearings

def e(v): return html.escape(str(v if v not in (None,'') else '-'))

async def workcontrol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s=supervision_summary()
    lines=["👩‍💼 <b>PRIYA — WORK CONTROL</b>","🧩 Sprint 14.0.1 AD Floor Resolution Fix","",
           f"📋 Pending: <b>{s.get('pending',0)}</b>",f"📅 Due Today: <b>{s.get('due_today',0)}</b>",
           f"🔴 Overdue: <b>{s.get('overdue',0)}</b>",f"🟡 Awaiting Verification: <b>{s.get('awaiting_verification',0)}</b>",
           f"✅ Verified Today: <b>{s.get('verified_today',0)}</b>","","<b>STAFF LOAD</b>"]
    for r in s.get('staff',[]): lines.append(f"👤 {e(r.get('staff'))}: {r.get('pending',0)} pending · {r.get('overdue',0)} overdue")
    lines += ["","Use /workboard to open pending Works."]
    await update.effective_message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML)

async def reconcileassignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count=reconcile_live_hearings()
    await update.effective_message.reply_text(f"✅ Advocate Diaries floor data refreshed and ownership recalculated for {count} live hearing case(s).")
