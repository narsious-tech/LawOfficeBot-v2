from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from advocate_web import AdvocateWeb
from commands.dashboard import fetch_advocate_diaries_cause_groups
from services.case_intelligence_service import staff_telegram_id

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 8
RECIPIENTS = ("Preet", "Priya", "Happy", "Jimmy")


def _target_date():
    return datetime.now(IST).date() + timedelta(days=1)


def _case_count(groups):
    return sum(len(group.get("cases") or []) for group in groups)


def _safe(value, fallback="-"):
    text = str(value or "").strip()
    return text or fallback


def _flatten_cases(groups):
    rows = []
    for group in groups:
        for case in group.get("cases") or []:
            rows.append({
                "case_number": _safe(case.get("case_number"), "Case number not entered"),
                "case_title": _safe(case.get("case_title"), "Title not recorded"),
                "purpose": _safe(case.get("stage") or case.get("purpose"), "Purpose not recorded"),
                "owner": _safe(case.get("owner_name") or case.get("owner"), "Not assigned"),
                "court": _safe(group.get("court_name"), "Court not recorded"),
                "judge": _safe(group.get("judge_name"), "Judge not recorded"),
                "floor": _safe(group.get("floor"), "-"),
                "room": _safe(group.get("room"), "-"),
            })
    return rows


def _physical_file_text(groups, target):
    lines = [
        "📁 PHYSICAL FILE PREPARATION",
        f"📅 {target.strftime('%d %b %Y')}",
        f"Tomorrow's hearings: {_case_count(groups)}",
        "",
        "Select only the physical files that staff must bring to the evening office.",
        "Use the case-wise buttons below, then press ‘Send selected files’."
    ]
    return "\n".join(lines)


def _selection_store(context, chat_id, target):
    all_states = context.application.bot_data.setdefault("evening_file_selections", {})
    return all_states.setdefault(f"{chat_id}:{target.isoformat()}", {"selected": set(), "cases": []})


def _selection_text(state, target, page=0):
    cases = state.get("cases") or []
    selected = state.get("selected") or set()
    pages = max(1, (len(cases) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(len(cases), start + PAGE_SIZE)
    lines = [
        "☑️ SELECT FILES TO BRING",
        f"📅 {target.strftime('%d %b %Y')}",
        f"Selected: {len(selected)} of {len(cases)}",
        f"Page: {page + 1}/{pages}",
        "",
    ]
    for idx in range(start, end):
        case = cases[idx]
        mark = "✅" if idx in selected else "⬜"
        lines.extend([
            f"{mark} {idx + 1}. {case['case_number']}",
            f"   {case['case_title']}",
            f"   {case['court']} | Floor {case['floor']} | Room {case['room']}",
            f"   Purpose: {case['purpose']}",
            "",
        ])
    if not cases:
        lines.append("No hearings were found for tomorrow.")
    return "\n".join(lines).strip()


def _selection_keyboard(state, target, page=0):
    cases = state.get("cases") or []
    selected = state.get("selected") or set()
    pages = max(1, (len(cases) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(len(cases), start + PAGE_SIZE)
    rows = []
    for idx in range(start, end):
        symbol = "✅" if idx in selected else "⬜"
        number = cases[idx]["case_number"]
        label = f"{symbol} {idx + 1}. {number}"[:55]
        rows.append([InlineKeyboardButton(label, callback_data=f"efs:{target.isoformat()}:t:{idx}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"efs:{target.isoformat()}:p:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"efs:{target.isoformat()}:p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        f"📤 Send {len(selected)} selected files",
        callback_data=f"efs:{target.isoformat()}:send:{page}",
    )])
    rows.append([InlineKeyboardButton("🧹 Clear selection", callback_data=f"efs:{target.isoformat()}:clear:{page}")])
    return InlineKeyboardMarkup(rows)


def _assigned_list_text(cases, selected, target, assigned_by):
    lines = [
        "📁 FILES TO BRING TO EVENING OFFICE",
        f"📅 {target.strftime('%d %b %Y')}",
        f"Assigned by: {assigned_by}",
        f"Total selected files: {len(selected)}",
        "",
    ]
    for serial, idx in enumerate(sorted(selected), 1):
        case = cases[idx]
        lines.extend([
            f"{serial}. {case['case_number']}",
            f"   {case['case_title']}",
            f"   Court: {case['court']}",
            f"   Judge: {case['judge']}",
            f"   Floor {case['floor']} | Room {case['room']}",
            f"   Purpose: {case['purpose']}",
            "",
        ])
    lines.append("Please arrange and bring only the above-selected physical files.")
    return "\n".join(lines).strip()


async def _official_pdf(target):
    pdf_bytes = await asyncio.to_thread(AdvocateWeb().download_day_cases_pdf, target.isoformat())
    path = os.path.join(tempfile.gettempdir(), f"Case-{target.isoformat()}.pdf")
    with open(path, "wb") as handle:
        handle.write(pdf_bytes)
    return path


async def printablecauselist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = datetime.now(IST).date()
    if context.args and context.args[0].lower() in ("tomorrow", "tom"):
        target += timedelta(days=1)
    try:
        path = await _official_pdf(target)
    except Exception as exc:
        logger.exception("Official Advocate Diaries PDF download failed")
        await update.effective_message.reply_text(f"⚠️ Official Advocate Diaries cause-list PDF could not be downloaded: {exc}")
        return
    with open(path, "rb") as file_handle:
        await update.effective_message.reply_document(file_handle, filename=os.path.basename(path), caption=f"Official Advocate Diaries cause list — {target.strftime('%d %b %Y')}")


async def eveningdashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_dashboard(context, chat_id=update.effective_chat.id, force=True)


async def filesready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Use the case-wise checkboxes in /eveningdashboard and press ‘Send selected files’. The selected list is sent to Preet, Priya, Happy and Jimmy.")


async def evening_file_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        parts = query.data.split(":")
        date_text = parts[1]
        action = parts[2]
        target = datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        await query.answer("Invalid selection", show_alert=True)
        return

    state = _selection_store(context, query.message.chat.id, target)
    cases = state.get("cases") or []
    selected = state.setdefault("selected", set())

    if action == "t":
        idx = int(parts[3])
        page = int(parts[4])
        if idx < 0 or idx >= len(cases):
            await query.answer("Case is no longer available", show_alert=True)
            return
        if idx in selected:
            selected.remove(idx)
        else:
            selected.add(idx)
        await query.edit_message_text(_selection_text(state, target, page), reply_markup=_selection_keyboard(state, target, page))
        return

    if action == "p":
        page = int(parts[3])
        await query.edit_message_text(_selection_text(state, target, page), reply_markup=_selection_keyboard(state, target, page))
        return

    if action == "clear":
        page = int(parts[3])
        selected.clear()
        await query.edit_message_text(_selection_text(state, target, page), reply_markup=_selection_keyboard(state, target, page))
        return

    if action == "send":
        page = int(parts[3])
        if not selected:
            await query.answer("Select at least one file first.", show_alert=True)
            return
        assigned_by = query.from_user.full_name or str(query.from_user.id)
        text = _assigned_list_text(cases, selected, target, assigned_by)
        delivered, missing = [], []
        for name in RECIPIENTS:
            telegram_id = await asyncio.to_thread(staff_telegram_id, name)
            if not telegram_id:
                missing.append(name)
                continue
            try:
                for start in range(0, len(text), 3900):
                    await context.bot.send_message(chat_id=telegram_id, text=text[start:start + 3900])
                delivered.append(name)
            except Exception:
                logger.exception("Could not send selected file list to %s", name)
                missing.append(name)
        confirmation = f"✅ Selected file list sent to: {', '.join(delivered) or 'nobody'}."
        if missing:
            confirmation += f"\n⚠️ Telegram account not linked/reachable: {', '.join(missing)}."
        await context.bot.send_message(chat_id=query.message.chat.id, text=confirmation)
        await query.edit_message_text(_selection_text(state, target, page), reply_markup=_selection_keyboard(state, target, page))
        return


# Backward-compatible old callback. Existing old dashboard buttons will not crash.
async def evening_file_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("This checklist has been replaced by case-wise file selection.", show_alert=True)


async def send_evening_dashboard(context, chat_id=None, force=False):
    target = _target_date()
    groups, source = await asyncio.to_thread(fetch_advocate_diaries_cause_groups, target)
    total = _case_count(groups)
    raw_destination = chat_id or os.getenv("OFFICE_GROUP_CHAT_ID") or os.getenv("PHYSICAL_FILE_GROUP_CHAT_ID")
    if not raw_destination:
        raise RuntimeError("OFFICE_GROUP_CHAT_ID or PHYSICAL_FILE_GROUP_CHAT_ID is required")
    destination = int(raw_destination)

    text = (
        "🌆 EVENING OPERATIONS DASHBOARD\n"
        f"📅 Tomorrow: {target.strftime('%d %b %Y')}\n"
        f"⚖️ Hearings: {total}\n"
        f"🔗 Cause-list source: Advocate Diaries {source}\n\n"
        "Select only the files that must be brought to the evening office. "
        "The final selected list will be sent to Preet, Priya, Happy and Jimmy."
    )
    await context.bot.send_message(chat_id=destination, text=text)
    await context.bot.send_message(chat_id=destination, text=_physical_file_text(groups, target))

    state = _selection_store(context, destination, target)
    state["cases"] = _flatten_cases(groups)
    state.setdefault("selected", set())
    await context.bot.send_message(
        chat_id=destination,
        text=_selection_text(state, target, 0),
        reply_markup=_selection_keyboard(state, target, 0),
    )

    try:
        path = await _official_pdf(target)
        with open(path, "rb") as file_handle:
            await context.bot.send_document(chat_id=destination, document=file_handle, filename=os.path.basename(path), caption="📎 Official Advocate Diaries cause list attached")
    except Exception as exc:
        logger.exception("Official cause-list PDF could not be attached")
        await context.bot.send_message(chat_id=destination, text=f"⚠️ Official Advocate Diaries PDF could not be attached: {exc}")


async def evening_dashboard_job(context):
    await send_evening_dashboard(context)
