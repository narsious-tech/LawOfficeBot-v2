"""Private, date-specific manual hearing allocation to Isha."""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.ext import CommandHandler, CallbackQueryHandler
from services.access_policy import configured_admin_ids, resolve_identity
from services.role_intelligence_service import _connect

IST = ZoneInfo('Asia/Kolkata')
log = logging.getLogger(__name__)


def authorized(user_id):
    if user_id in configured_admin_ids():
        return True
    identity = resolve_identity(user_id)
    return bool(identity.linked and str(identity.name or '').strip().casefold() == 'priya')


def load_cases(target):
    from commands.dashboard import fetch_advocate_diaries_cause_groups
    from commands.evening_dashboard import _flatten_cases
    from services.judge_leave_service import apply_leave_exclusions
    groups, _ = fetch_advocate_diaries_cause_groups(target)
    groups, _, _ = apply_leave_exclusions(groups, target)
    rows = _flatten_cases(groups)
    seen, result = set(), []
    for row in rows:
        key = case_key(row)
        if key not in seen:
            seen.add(key)
            row['key'] = key
            result.append(row)
    return result


def case_key(row):
    # Include court and title: identical short numbers can belong to different courts.
    values = [str(row.get(k) or '').strip().casefold() for k in ('case_number', 'case_title', 'court', 'judge')]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def persist(state, actor):
    with _connect() as con, con.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS associate_daily_allocations (
            batch_id TEXT PRIMARY KEY, target_date DATE NOT NULL,
            associate_name TEXT NOT NULL, assigned_by BIGINT NOT NULL,
            cases JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())''')
        cur.execute('''INSERT INTO associate_daily_allocations
            (batch_id,target_date,associate_name,assigned_by,cases)
            VALUES (%s,%s,'Isha',%s,%s) ON CONFLICT(batch_id) DO NOTHING''',
            (state['id'], state['date'], actor, json.dumps(state['final'])))


def report(target, rows):
    lines = ["⚖️ ISHA — ASSIGNED COURT APPEARANCES", f"📅 {target:%d-%m-%Y}", f"Matters: {len(rows)}", '']
    for i, row in enumerate(rows, 1):
        lines += [f"{i}. {row['case_number']}", row['case_title'],
                  f"Court: {row['court']} · Judge: {row['judge']}",
                  f"Floor: {row['floor']} · Room: {row['room']}",
                  f"Purpose: {row['purpose']}", '']
    return '\n'.join(lines)


def chunks(text, limit=3500):
    while text:
        end = len(text) if len(text) <= limit else text.rfind('\n', 0, limit)
        if end <= 0:
            end = limit
        yield text[:end]
        text = text[end:].lstrip('\n')


def board(state, page=0):
    rows = state['cases']
    page = min(max(page, 0), max(0, (len(rows)-1)//5))
    start = page*5
    lines = [f"⚖️ SELECT ISHA'S CASES · {state['date']:%d-%m-%Y}",
             f"Selected: {len(state['selected'])} · Page {page+1}/{max(1,(len(rows)+4)//5)}", '']
    buttons = []
    for i, row in enumerate(rows[start:start+5], start):
        mark = '✅' if i in state['selected'] else '⬜'
        lines += [f"{mark} {i+1}. {row['case_number']}", row['case_title'][:180],
                  f"{row['judge']} · {row['purpose']}", '']
        buttons.append([Button(f"{mark} {i+1}. {row['case_number']}"[:60], callback_data=f"ia:{state['id']}:t:{i}:{page}")])
    nav = []
    if page: nav.append(Button('Previous', callback_data=f"ia:{state['id']}:p:{page-1}"))
    if start+5 < len(rows): nav.append(Button('Next', callback_data=f"ia:{state['id']}:p:{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([Button('Review selected', callback_data=f"ia:{state['id']}:r"), Button('Cancel', callback_data=f"ia:{state['id']}:x")])
    return '\n'.join(lines), Markup(buttons)


async def allocate(update, context):
    if update.effective_chat.type != 'private' or not authorized(update.effective_user.id):
        await update.effective_message.reply_text('Allocation is available privately to Ajay and Priya.')
        return
    try:
        target = datetime.now(IST).date()
        if context.args: target = datetime.strptime(context.args[0], '%d-%m-%Y').date()
        if not datetime.now(IST).date() <= target <= datetime.now(IST).date()+timedelta(days=14):
            raise ValueError()
    except ValueError:
        await update.effective_message.reply_text('Use /assignisha DD-MM-YYYY (today or next 14 days).')
        return
    await update.effective_message.reply_text('Loading the cause list…')
    try:
        rows = await asyncio.to_thread(load_cases, target)
        if not rows:
            await update.effective_message.reply_text('No hearings available after confirmed judge-leave exclusions.')
            return
        state = {'id': uuid.uuid4().hex[:12], 'date': target, 'cases': rows, 'selected': set(), 'phase': 'select'}
        context.user_data['isha_allocation'] = state
        text, keyboard = board(state)
        await update.effective_message.reply_text(text, reply_markup=keyboard)
    except Exception:
        log.exception('Associate cause list failed')
        await update.effective_message.reply_text('The cause list could not be loaded. No assignments were sent.')


async def callback(update, context):
    q = update.callback_query
    if update.effective_chat.type != 'private' or not authorized(q.from_user.id):
        await q.answer('Not authorized', show_alert=True); return
    await q.answer()
    state = context.user_data.get('isha_allocation')
    parts = q.data.split(':')
    if not state or parts[1] != state['id'] or state['phase'] in ('sending', 'sent', 'cancelled'):
        await q.message.reply_text('This selection is closed. Start /assignisha again.'); return
    action = parts[2]
    if action == 'x':
        state['phase'] = 'cancelled'
        await q.edit_message_text('Cancelled. No new list will be sent.'); return
    if action in ('t', 'p'):
        state['phase'] = 'select'
        if action == 't':
            index = int(parts[3])
            if not 0 <= index < len(state['cases']): return
            state['selected'].symmetric_difference_update({index})
        page = int(parts[4] if action == 't' else parts[3])
        text, keyboard = board(state, page)
        await q.edit_message_text(text, reply_markup=keyboard); return
    if action == 'r':
        if not state['selected']:
            await q.message.reply_text('Select at least one case first.'); return
        state['phase'] = 'review'
        for text in chunks(report(state['date'], [state['cases'][i] for i in sorted(state['selected'])])):
            await q.message.reply_text(text)
        await q.edit_message_reply_markup(reply_markup=Markup([[Button('Confirm and send to Isha', callback_data=f"ia:{state['id']}:s")], [Button('Back', callback_data=f"ia:{state['id']}:p:0"), Button('Cancel', callback_data=f"ia:{state['id']}:x")]])); return
    if action != 's' or state['phase'] != 'review': return
    state['phase'] = 'sending'
    try:
        # Reload at confirmation so new leave notices/removed hearings cannot be sent.
        fresh = {r['key']: r for r in await asyncio.to_thread(load_cases, state['date'])}
        selected = [state['cases'][i] for i in sorted(state['selected'])]
        if any(row['key'] not in fresh for row in selected):
            state['phase'] = 'select'
            await q.message.reply_text('The cause list or judge leave has changed. Start /assignisha again and review the refreshed list.'); return
        state['final'] = [fresh[row['key']] for row in selected]
        from services.staff_notification_delivery import staff_recipients, deliver_staff_whatsapp
        recipients = await asyncio.to_thread(staff_recipients, ['Isha'])
        if len(recipients) != 1:
            state['phase'] = 'review'
            await q.message.reply_text('Exactly one active Isha profile is required. No list sent.'); return
        await asyncio.to_thread(persist, state, q.from_user.id)
        body = report(state['date'], state['final'])
        results = []
        recipient = recipients[0]
        try:
            if not recipient.get('telegram_user_id'): raise ValueError('No Telegram link')
            for text in chunks(body): await context.bot.send_message(recipient['telegram_user_id'], text)
            results.append('Telegram: sent')
        except Exception:
            log.exception('Associate Telegram delivery failed')
            results.append('Telegram: failed or not linked; check before resending')
        expiry = datetime.combine(state['date']+timedelta(days=1), datetime.min.time(), IST)
        wa = await asyncio.to_thread(deliver_staff_whatsapp, body, 'isha-allocation:'+state['id'], ['Isha'], expiry)
        results.append('WhatsApp: '+ ('sent' if wa['sent'] else 'queued until Isha messages the bot' if wa['queued'] else 'failed or not linked'))
        state['phase'] = 'sent'
        await q.edit_message_text('✅ Allocation saved.\n'+'\n'.join(results))
    except Exception:
        state['phase'] = 'sent'  # Do not blindly repeat potentially partial deliveries.
        log.exception('Associate allocation failed')
        await q.message.reply_text('Allocation could not complete. Delivery may be partial; check with Isha before starting another batch.')


def register_associate_allocation(app):
    app.add_handler(CommandHandler('assignisha', allocate))
    app.add_handler(CallbackQueryHandler(callback, pattern=r'^ia:'))
