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

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _target_date():
    return datetime.now(IST).date() + timedelta(days=1)


def _case_count(groups):
    return sum(len(group.get("cases") or []) for group in groups)


def _safe(value, fallback="-"):
    text = str(value or "").strip()
    return text or fallback


def _physical_file_text(groups, target):
    lines = [
        "📁 PHYSICAL FILE PREPARATION",
        f"📅 {target.strftime('%d %b %Y')}",
        f"Total files: {_case_count(groups)}",
        "",
    ]
    serial = 1
    for group in groups:
        court = _safe(group.get("court_name"), "Court not recorded")
        floor = _safe(group.get("floor"), "-")
        room = _safe(group.get("room"), "-")
        judge = _safe(group.get("judge_name"), "Judge not recorded")
        lines.append(f"⚖️ {court} | Floor {floor} | Room {room}")
        lines.append(f"Judge: {judge}")
        for case in group.get("cases") or []:
            number = _safe(case.get("case_number"), "Case number not entered")
            title = _safe(case.get("case_title"), "Title not recorded")
            purpose = _safe(case.get("stage") or case.get("purpose"), "Purpose not recorded")
            owner = _safe(case.get("owner_name") or case.get("owner"), "Not assigned")
            lines.extend([
                f"{serial}. {number}",
                f"   {title}",
                f"   Purpose: {purpose} | Owner: {owner}",
            ])
            serial += 1
        lines.append("")

    lines.extend([
        "✅ FILE-PREPARATION CHECK-INS",
        "☐ Files removed from cupboard",
        "☐ Previous orders and brief checked",
        "☐ Fresh documents/evidence attached",
        "☐ Files placed in tomorrow's tray",
    ])
    return "\n".join(lines).strip()


def _checkin_keyboard(target):
    key = target.isoformat()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Files removed", callback_data=f"efd:{key}:removed")],
        [InlineKeyboardButton("2️⃣ Briefs/orders checked", callback_data=f"efd:{key}:checked")],
        [InlineKeyboardButton("3️⃣ Tomorrow tray ready", callback_data=f"efd:{key}:ready")],
    ])


def _state(context, chat_id, target):
    all_states = context.application.bot_data.setdefault("evening_file_checkins", {})
    return all_states.setdefault(f"{chat_id}:{target.isoformat()}", {})


def _status_text(state):
    labels = [
        ("removed", "Files removed from cupboard"),
        ("checked", "Briefs/orders/documents checked"),
        ("ready", "Files placed in tomorrow's tray"),
    ]
    lines = ["📁 PHYSICAL FILE READINESS"]
    for key, label in labels:
        item = state.get(key)
        if item:
            lines.append(f"✅ {label} — {item['by']} at {item['time']}")
        else:
            lines.append(f"☐ {label}")
    completed = sum(1 for key, _ in labels if state.get(key))
    lines.append(f"\nProgress: {completed}/3")
    return "\n".join(lines)


async def _official_pdf(target):
    pdf_bytes = await asyncio.to_thread(
        AdvocateWeb().download_day_cases_pdf, target.isoformat()
    )
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
        await update.effective_message.reply_text(
            f"⚠️ Official Advocate Diaries cause-list PDF could not be downloaded: {exc}"
        )
        return
    with open(path, "rb") as file_handle:
        await update.effective_message.reply_document(
            file_handle,
            filename=os.path.basename(path),
            caption=f"Official Advocate Diaries cause list — {target.strftime('%d %b %Y')}",
        )


async def eveningdashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_dashboard(context, chat_id=update.effective_chat.id, force=True)


async def filesready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = _target_date()
    state = _state(context, update.effective_chat.id, target)
    now = datetime.now(IST)
    by = update.effective_user.full_name or str(update.effective_user.id)
    for key in ("removed", "checked", "ready"):
        state[key] = {"by": by, "time": now.strftime("%I:%M %p")}
    await update.effective_message.reply_text(
        _status_text(state), reply_markup=_checkin_keyboard(target)
    )


async def evening_file_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, date_text, step = query.data.split(":", 2)
        target = datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        await query.answer("Invalid check-in", show_alert=True)
        return
    state = _state(context, query.message.chat.id, target)
    now = datetime.now(IST)
    by = query.from_user.full_name or str(query.from_user.id)
    state[step] = {"by": by, "time": now.strftime("%I:%M %p")}
    await query.edit_message_text(
        _status_text(state), reply_markup=_checkin_keyboard(target)
    )


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
        f"⚖️ Hearings / physical files: {total}\n"
        f"🔗 Cause-list source: Advocate Diaries {source}\n\n"
        "Jimmy: use the check-ins below while preparing tomorrow's physical files."
    )
    await context.bot.send_message(chat_id=destination, text=text)

    physical_text = _physical_file_text(groups, target)
    for start in range(0, len(physical_text), 3900):
        await context.bot.send_message(chat_id=destination, text=physical_text[start:start + 3900])

    state = _state(context, destination, target)
    await context.bot.send_message(
        chat_id=destination,
        text=_status_text(state),
        reply_markup=_checkin_keyboard(target),
    )

    try:
        path = await _official_pdf(target)
        with open(path, "rb") as file_handle:
            await context.bot.send_document(
                chat_id=destination,
                document=file_handle,
                filename=os.path.basename(path),
                caption="📎 Official Advocate Diaries cause list attached",
            )
    except Exception as exc:
        logger.exception("Official cause-list PDF could not be attached")
        await context.bot.send_message(
            chat_id=destination,
            text=f"⚠️ Official Advocate Diaries PDF could not be attached: {exc}",
        )


async def evening_dashboard_job(context):
    await send_evening_dashboard(context)
