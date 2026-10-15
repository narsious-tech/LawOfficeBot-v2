"""Private owner preview and confirmation for existing AD work case links."""
import asyncio
import secrets
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler
from services.access_policy import configured_admin_ids
from services.ad_work_link_repair import load_orphans, suggest_case_context, task_snapshot, apply_confirmed_context


def _allowed(update):
    return bool(update.effective_chat and update.effective_chat.type=='private' and update.effective_user and update.effective_user.id in configured_admin_ids())


def _records():
    from advocate_web import AdvocateWeb
    from services.ad_work_service import fetch_works
    records,status=fetch_works(AdvocateWeb(),'pending')
    if status!=200:
        raise ValueError(f'Advocate Diaries work fetch failed ({status}).')
    return records


def _preview(task,candidate):
    source_id=candidate.get('work_id') or 'Not supplied by AD — case details only'
    return (f"🔧 REPAIR EXISTING WORK — PREVIEW\nTask #{task['id']} · {task.get('assigned_to') or 'Unassigned'}\n"
            f"📝 {task['task']}\n\nCandidate case:\n⚖️ {candidate['case_title']}\n🔢 {candidate['case_number']}\n"
            f"👤 Client: {candidate.get('client') or '-'}\nAD Work ID: {source_id}\n\n"
            'Confirm only if this is the correct case for this existing task. Assignment and completion status are retained.')


async def repairworklinks(update,context):
    if not _allowed(update):
        await update.effective_message.reply_text('⛔ Owner-only repair. Use your private bot chat.')
        return
    if context.args and (len(context.args)!=1 or not context.args[0].isdigit()):
        await update.effective_message.reply_text('Use /repairworklinks or /repairworklinks 61')
        return
    try:
        records=await asyncio.to_thread(_records)
        tasks=await asyncio.to_thread(load_orphans,int(context.args[0]) if context.args else None)
        store=context.user_data.setdefault('ad_work_repairs',{})
        now=time.monotonic()
        for key in list(store):
            if now-store[key]['created']>600: del store[key]
        if not tasks:
            await update.effective_message.reply_text('No pending imported tasks with missing case context were found.')
        for task in tasks:
            candidates=suggest_case_context(task,records)
            if not candidates:
                await update.effective_message.reply_text(f"Task #{task['id']}: no exact-description candidate with case details. Nothing changed.")
                continue
            token=secrets.token_urlsafe(10)
            store[token]={'task':task,'expected':task_snapshot(task),'candidates':candidates,'created':now,'chosen':None}
            buttons=[[InlineKeyboardButton(f"{index+1}. {c['case_title'][:45]} · {c['case_number']}"[:95], callback_data=f'awr:choose:{token}:{index}')] for index,c in enumerate(candidates[:10])]
            await update.effective_message.reply_text(f"🔧 Task #{task['id']} · {task.get('assigned_to')}\n📝 {task['task']}\nChoose the correct case to preview. Nothing changes yet.",reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as exc:
        await update.effective_message.reply_text(f'⚠️ Repair preview unavailable: {str(exc)[:500]}')


async def repair_callback(update,context):
    q=update.callback_query
    await q.answer()
    if not _allowed(update): return
    parts=(q.data or '').split(':')
    store=context.user_data.setdefault('ad_work_repairs',{})
    entry=store.get(parts[2]) if len(parts)>2 else None
    if not entry or time.monotonic()-entry['created']>600:
        await q.message.reply_text('Preview expired or already used. Run /repairworklinks again.')
        return
    token=parts[2]
    if parts[1]=='cancel':
        store.pop(token,None)
        await q.edit_message_text('Cancelled. No task changed.')
        return
    if parts[1]=='choose' and len(parts)==4 and parts[3].isdigit() and int(parts[3])<len(entry['candidates']):
        entry['chosen']=int(parts[3])
        await q.edit_message_text(_preview(entry['task'],entry['candidates'][entry['chosen']]),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Confirm repair',callback_data=f'awr:confirm:{token}'),InlineKeyboardButton('Cancel',callback_data=f'awr:cancel:{token}')]]))
        return
    if parts[1]!='confirm' or entry['chosen'] is None: return
    store.pop(token,None)
    try:
        records=await asyncio.to_thread(_records)
        result=await asyncio.to_thread(apply_confirmed_context,entry['task']['id'],entry['candidates'][entry['chosen']],entry['expected'],update.effective_user.id,records)
        await q.edit_message_text(f"✅ Task #{entry['task']['id']} case details repaired\n⚖️ {result['notes']}\n🔢 {result['case_number']}\nStaff can refresh /mytasks or WhatsApp MY WORK.")
    except Exception as exc:
        await q.message.reply_text(f'⚠️ Repair not completed: {str(exc)[:500]}\nRun /repairworklinks for a fresh preview.')


def register_ad_work_repair_handlers(app):
    app.add_handler(CommandHandler('repairworklinks',repairworklinks))
    app.add_handler(CallbackQueryHandler(repair_callback,pattern=r'^awr:'))
