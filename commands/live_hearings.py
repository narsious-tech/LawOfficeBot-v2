from __future__ import annotations

from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from services.live_hearing_service import (
    complete_live_hearing,
    get_live_hearing,
    list_live_hearings,
    set_live_hearing_status,
    sync_live_hearings,
)

IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 8
STATUS_LABELS = {
    "LISTED": "⚪ Listed", "CALLED": "🟢 Called", "PASSED_OVER": "🟡 Passed Over",
    "ADJOURNED": "🔵 Adjourned", "ORDER_RESERVED": "🟣 Order Reserved", "DISPOSED": "✅ Disposed",
}


def _page_bounds(rows, page: int):
    pages = max(1, ceil(len(rows) / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    return page, pages, rows[start:start + PAGE_SIZE]


def _board_keyboard(rows, page=0):
    page, pages, visible = _page_bounds(rows, page)
    buttons = [[InlineKeyboardButton(
        f"{STATUS_LABELS.get(r.get('status'), '⚪')} #{r['id']} {r.get('case_number') or 'Open'}",
        callback_data=f"lhc:open:{r['id']}:{page}",
    )] for r in visible]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"lhc:page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data=f"lhc:page:{page}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"lhc:page:{page+1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔄 Sync & Refresh", callback_data=f"lhc:refresh:{page}")])
    return InlineKeyboardMarkup(buttons)


def _detail_keyboard(hearing_id, page=0):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Called", callback_data=f"lhc:set:{hearing_id}:CALLED:{page}"), InlineKeyboardButton("🟡 Passed Over", callback_data=f"lhc:set:{hearing_id}:PASSED_OVER:{page}")],
        [InlineKeyboardButton("🔵 Adjourned", callback_data=f"lhc:set:{hearing_id}:ADJOURNED:{page}"), InlineKeyboardButton("🟣 Reserved", callback_data=f"lhc:set:{hearing_id}:ORDER_RESERVED:{page}")],
        [InlineKeyboardButton("✅ Complete Hearing", callback_data=f"lhc:complete:{hearing_id}:{page}"), InlineKeyboardButton("⚪ Reset Listed", callback_data=f"lhc:set:{hearing_id}:LISTED:{page}")],
        [InlineKeyboardButton("⬅️ Live Board", callback_data=f"lhc:page:{page}")],
    ])


def _board_text(rows, source=None, page=0):
    now = datetime.now(IST)
    page, pages, visible = _page_bounds(rows, page)
    lines = ["⚖️ LIVE HEARING CONTROL", f"📅 {now:%d-%m-%Y}  •  🕘 {now:%I:%M %p} IST", "🧩 Sprint 12.2.1 Stability Release"]
    if source:
        lines.append(f"🔗 Synced via Advocate Diaries {source}")
    lines.append("")
    if not rows:
        return "\n".join(lines + ["No hearings are listed for today."])
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines += [
        f"📌 Total {len(rows)}   🟢 Called {counts.get('CALLED', 0)}   🟡 Passed {counts.get('PASSED_OVER', 0)}",
        f"✅ Closed {sum(counts.get(x, 0) for x in ('ADJOURNED', 'ORDER_RESERVED', 'DISPOSED'))}",
        f"📄 Page {page + 1}/{pages} • Showing {page * PAGE_SIZE + 1}-{page * PAGE_SIZE + len(visible)}", "",
    ]
    for r in visible:
        location = " / ".join(x for x in [r.get("floor") and f"Floor {r['floor']}", r.get("room") and f"Room {r['room']}"] if x)
        lines += [
            f"{STATUS_LABELS.get(r['status'], r['status'])}  #{r['id']}",
            f"{r.get('case_number') or '-'} — {r.get('case_title') or '-'}",
            f"👨‍⚖️ {r.get('judge_name') or r.get('court_name') or '-'}" + (f" | {location}" if location else ""), "",
        ]
    return "\n".join(lines).strip()


def _detail_text(r):
    return "\n".join([
        "⚖️ LIVE HEARING", "", f"🔢 {r.get('case_number') or '-'}", f"📝 {r.get('case_title') or '-'}",
        f"📍 Stage: {r.get('stage') or '-'}", f"👨‍⚖️ {r.get('judge_name') or '-'}", f"🏛 {r.get('court_name') or '-'}",
        f"📌 Floor {r.get('floor') or '-'} | Room {r.get('room') or '-'}", f"📊 Status: {STATUS_LABELS.get(r.get('status'), r.get('status'))}", "", "Select the current hearing status.",
    ])


async def livehearings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        _, source = sync_live_hearings()
        rows = list_live_hearings()
        await update.effective_message.reply_text(_board_text(rows, source, 0), reply_markup=_board_keyboard(rows, 0))
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Live hearing board failed:\n{type(exc).__name__}: {exc}")


async def live_hearing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    parts = (q.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "page"
    try:
        if action in {"board", "page", "refresh"}:
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            source = None
            if action == "refresh":
                _, source = sync_live_hearings()
            rows = list_live_hearings()
            await q.edit_message_text(_board_text(rows, source, page), reply_markup=_board_keyboard(rows, page))
            return
        if action == "open":
            row = get_live_hearing(int(parts[2])); page = int(parts[3]) if len(parts) > 3 else 0
            if not row:
                await q.edit_message_text("Hearing not found."); return
            await q.edit_message_text(_detail_text(row), reply_markup=_detail_keyboard(row["id"], page)); return
        if action == "set":
            row = set_live_hearing_status(int(parts[2]), parts[3], q.from_user.id); page = int(parts[4]) if len(parts) > 4 else 0
            if not row:
                await q.edit_message_text("Hearing not found."); return
            await q.edit_message_text(_detail_text(row), reply_markup=_detail_keyboard(row["id"], page)); return
    except Exception as exc:
        if "Message is not modified" in str(exc):
            return
        await q.edit_message_text(f"❌ Live hearing update failed:\n{type(exc).__name__}: {exc}")


NEXT_DATE, PURPOSE, ORDER, DOCUMENTS, CREATE_TASK, NOTIFY, CONFIRM = range(7)

async def completion_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = (q.data or "").split(":")
    hearing_id = int(parts[2]); page = int(parts[3]) if len(parts) > 3 else 0
    row = get_live_hearing(hearing_id)
    if not row:
        await q.edit_message_text("Hearing not found."); return ConversationHandler.END
    context.user_data["hearing_completion"] = {"hearing_id": hearing_id, "page": page}
    await q.edit_message_text(f"✅ HEARING COMPLETION\n\n🔢 {row.get('case_number') or '-'}\n📝 {row.get('case_title') or '-'}\n\nEnter the next date as DD-MM-YYYY.\nSend /none if disposed or no next date is fixed.")
    return NEXT_DATE

async def completion_next_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text.lower() in {"/none", "none", "nil", "-"}:
        value = None
    else:
        try:
            value = datetime.strptime(text, "%d-%m-%Y").date()
        except ValueError:
            await update.effective_message.reply_text("❌ Use DD-MM-YYYY, for example 25-07-2026, or /none."); return NEXT_DATE
    context.user_data["hearing_completion"]["next_date"] = value
    await update.effective_message.reply_text("Enter the purpose/stage for the next hearing, or /none."); return PURPOSE

async def completion_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip(); context.user_data["hearing_completion"]["purpose"] = "" if text.lower() == "/none" else text
    await update.effective_message.reply_text("Enter a short order/outcome summary."); return ORDER

async def completion_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hearing_completion"]["order"] = (update.effective_message.text or "").strip()
    await update.effective_message.reply_text("List documents or preparation required before the next date. Send /none if nothing is required."); return DOCUMENTS

async def completion_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip(); context.user_data["hearing_completion"]["documents"] = "" if text.lower() == "/none" else text
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Create task", callback_data="lhcw:task:yes"), InlineKeyboardButton("Skip task", callback_data="lhcw:task:no")]])
    await update.effective_message.reply_text("Create a follow-up task from the required preparation?", reply_markup=kb); return CREATE_TASK

async def completion_task_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); context.user_data["hearing_completion"]["create_task"] = q.data.endswith(":yes")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📲 Queue client update", callback_data="lhcw:notify:yes"), InlineKeyboardButton("Internal only", callback_data="lhcw:notify:no")]])
    await q.edit_message_text("Should this outcome be flagged for a client update?", reply_markup=kb); return NOTIFY

async def completion_notify_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = context.user_data["hearing_completion"]; data["notify"] = q.data.endswith(":yes")
    summary = (f"✅ CONFIRM HEARING OUTCOME\n\n📅 Next date: {data.get('next_date') or '-'}\n📍 Purpose: {data.get('purpose') or '-'}\n📝 Order: {data.get('order') or '-'}\n📂 Preparation: {data.get('documents') or '-'}\n✅ Create task: {'Yes' if data.get('create_task') else 'No'}\n📲 Client update: {'Yes' if data.get('notify') else 'No'}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Save completion", callback_data="lhcw:confirm"), InlineKeyboardButton("❌ Cancel", callback_data="lhcw:cancel")]])
    await q.edit_message_text(summary, reply_markup=kb); return CONFIRM

async def completion_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Saving…")
    if q.data.endswith("cancel"):
        context.user_data.pop("hearing_completion", None); await q.edit_message_text("Hearing completion cancelled."); return ConversationHandler.END
    data = context.user_data.get("hearing_completion")
    if not data or "hearing_id" not in data:
        await q.edit_message_text("❌ Completion session expired. Open the hearing and try again."); return ConversationHandler.END
    try:
        result = complete_live_hearing(data["hearing_id"], next_date=data.get("next_date"), next_purpose=data.get("purpose", ""), order_summary=data.get("order", ""), documents_required=data.get("documents", ""), create_task=data.get("create_task", False), notify_client=data.get("notify", False), changed_by=q.from_user.id)
        if not result:
            raise RuntimeError("Hearing record was not found")
        context.user_data.pop("hearing_completion", None)
        warnings = result.get("warnings") or []
        lines = ["✅ HEARING COMPLETION SAVED", "", f"🔢 {result.get('case_number') or '-'}", f"📊 Status: {STATUS_LABELS.get(result.get('status'), result.get('status'))}", f"📅 Next date: {result.get('next_date') or '-'}"]
        if result.get("task_id"):
            lines.append(f"📋 Follow-up task: #{result['task_id']}")
        if result.get("notify_client"):
            lines.append("📲 Client update flagged")
        ad_status = result.get("ad_sync_status")
        if ad_status == "SUCCESS":
            lines.append("✅ Advocate Diaries updated")
        elif ad_status == "QUEUED":
            lines.append("⚠️ Advocate Diaries sync queued")
            if result.get("ad_sync_message"):
                lines.append(f"   {result['ad_sync_message']}")
        elif result.get("ad_sync_message"):
            lines.append(f"⚠️ Advocate Diaries: {result['ad_sync_message']}")
        if warnings:
            lines += ["", "⚠️ Non-critical notices:"] + [f"• {w}" for w in warnings]
        page = int(data.get("page", 0))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Live Board", callback_data=f"lhc:page:{page}"), InlineKeyboardButton("🔄 Refresh Board", callback_data=f"lhc:refresh:{page}")]])
        await q.edit_message_text("\n".join(lines), reply_markup=kb)
        return ConversationHandler.END
    except Exception as exc:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Try Save Again", callback_data="lhcw:confirm"), InlineKeyboardButton("❌ Cancel", callback_data="lhcw:cancel")]])
        await q.edit_message_text(f"❌ SAVE FAILED\n\n{type(exc).__name__}: {exc}\n\nThe entered information is still retained. You may retry.", reply_markup=kb)
        return CONFIRM

async def completion_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("hearing_completion", None); await update.effective_message.reply_text("Hearing completion cancelled."); return ConversationHandler.END


def hearing_completion_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(completion_start, pattern=r"^lhc:complete:\d+(?::\d+)?$")],
        states={
            NEXT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, completion_next_date), CommandHandler("none", completion_next_date)],
            PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, completion_purpose), CommandHandler("none", completion_purpose)],
            ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, completion_order)],
            DOCUMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, completion_documents), CommandHandler("none", completion_documents)],
            CREATE_TASK: [CallbackQueryHandler(completion_task_choice, pattern=r"^lhcw:task:")],
            NOTIFY: [CallbackQueryHandler(completion_notify_choice, pattern=r"^lhcw:notify:")],
            CONFIRM: [CallbackQueryHandler(completion_confirm, pattern=r"^lhcw:(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", completion_cancel)], allow_reentry=True,
    )
