"""Telegram judge-leave PDF workflow and pre-file notification jobs."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from commands.dashboard import fetch_advocate_diaries_cause_groups
from services.access_policy import configured_admin_ids, resolve_identity
from services.judge_leave_service import (
    apply_leave_exclusions,
    create_leave_batch,
    decide_batch,
    extract_pdf_text,
    holiday_greeting,
    leave_alert_text,
    mark_notification_sent,
    match_leave_groups,
    notification_was_sent,
)
from services.office_calendar_service import (
    manual_evening_plan,
    scheduled_evening_plan,
    working_saturday_monday_plan,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
WAIT_LEAVE_PDF, WAIT_LEAVE_CONFIRM = range(9050, 9052)
AUTHORIZED_NAMES = {"ajay", "priya", "preet", "isha"}
ALERT_STAFF_NAMES = ("Priya", "Preet", "Isha")


def _authorized(user_id: int | None) -> tuple[bool, str]:
    if user_id is not None and int(user_id) in configured_admin_ids():
        return True, "Ajay"
    identity = resolve_identity(user_id)
    first = str(identity.name or "").strip().casefold().split(" ", 1)[0]
    return bool(identity.linked and first in AUTHORIZED_NAMES), identity.name


def _parse_target(args: list[str]) -> datetime.date:
    today = datetime.now(IST).date()
    if not args:
        return manual_evening_plan(today).target_date
    token = args[0].strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            target = datetime.strptime(token, fmt).date()
            if target < today or target > today + timedelta(days=7):
                raise ValueError("Judge leave can be recorded only for today or the next 7 days.")
            return target
        except ValueError as exc:
            if "next 7 days" in str(exc):
                raise
    raise ValueError("Use /judgeleave or /judgeleave DD-MM-YYYY.")


def _preview_text(batch: dict) -> str:
    lines = [
        "⚠️ CONFIRM JUDGE LEAVE PDF",
        f"📅 Court date: {batch['target_date']:%d-%m-%Y}",
        f"📄 {batch.get('filename') or 'Leave PDF'}",
        "",
    ]
    if not batch.get("date_mentioned"):
        lines.extend([
            "🟠 The selected court date was not clearly found in the extracted PDF text.",
            "Please verify the date carefully before confirming.",
            "",
        ])
    total = 0
    for match in batch.get("matches") or []:
        total += int(match.get("case_count") or 0)
        lines.append(
            f"• {match.get('judge_name') or '-'} · {match.get('court_name') or 'Court'} "
            f"· {int(match.get('case_count') or 0)} matter(s)"
        )
        for case in (match.get("cases") or [])[:3]:
            lines.append(
                f"   - {case.get('case_number') or '-'} · "
                f"{case.get('case_title') or 'Title not recorded'}"
            )
        if len(match.get("cases") or []) > 3:
            lines.append(f"   - …and {len(match['cases']) - 3} more")
    lines.extend([
        "",
        f"📁 Total matters to ignore for file selection: {total}",
        "No case date or Advocate Diaries record will be changed.",
    ])
    return "\n".join(lines)[:3900]


async def judgeleave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, name = await asyncio.to_thread(_authorized, update.effective_user.id)
    if not allowed or update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "⛔ Judge-leave intake is available only in a private bot chat to Ajay, Priya, Preet or Isha."
        )
        return ConversationHandler.END
    try:
        target = _parse_target(context.args)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
        return ConversationHandler.END
    context.user_data["judge_leave_target"] = target.isoformat()
    context.user_data["judge_leave_actor"] = name
    await update.effective_message.reply_text(
        "👨‍⚖️ JUDGE LEAVE INTAKE\n\n"
        f"Court date: {target:%d-%m-%Y}\n"
        "Send the official judge-leave PDF now. The bot will compare its text with the "
        "cause list and show every judge and affected matter before anything is excluded.\n\n"
        "Use /cancel to stop."
    )
    return WAIT_LEAVE_PDF


async def judgeleave_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, actor_name = await asyncio.to_thread(_authorized, update.effective_user.id)
    if not allowed:
        await update.effective_message.reply_text("⛔ You are not authorized for judge-leave intake.")
        return ConversationHandler.END
    document = update.effective_message.document
    if not document or not (
        str(document.mime_type or "").lower() == "application/pdf"
        or str(document.file_name or "").lower().endswith(".pdf")
    ):
        await update.effective_message.reply_text("❌ Please send a PDF file.")
        return WAIT_LEAVE_PDF
    if int(document.file_size or 0) > 12 * 1024 * 1024:
        await update.effective_message.reply_text("❌ The PDF is larger than 12 MB.")
        return WAIT_LEAVE_PDF
    processing = await update.effective_message.reply_text("Reading the PDF and matching tomorrow's judges…")
    try:
        telegram_file = await document.get_file()
        data = bytes(await telegram_file.download_as_bytearray())
        text = await asyncio.to_thread(extract_pdf_text, data)
        target = datetime.fromisoformat(context.user_data["judge_leave_target"]).date()
        groups, _ = await asyncio.to_thread(fetch_advocate_diaries_cause_groups, target)
        matches = match_leave_groups(text, groups)
        batch = await asyncio.to_thread(
            create_leave_batch,
            target_date=target,
            pdf_bytes=data,
            filename=document.file_name or "judge-leave.pdf",
            pdf_text=text,
            matches=matches,
            source_channel="TELEGRAM",
            source_identity=f"telegram:{update.effective_user.id}",
            actor_name=actor_name,
            actor_telegram_id=update.effective_user.id,
        )
    except Exception as exc:
        logger.exception("Judge-leave PDF processing failed")
        await processing.edit_text(f"❌ Judge-leave PDF could not be prepared safely:\n{exc}")
        return WAIT_LEAVE_PDF
    context.user_data["judge_leave_batch"] = int(batch["id"])
    await processing.edit_text(
        _preview_text(batch),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm exclusions", callback_data=f"jl:confirm:{batch['id']}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"jl:cancel:{batch['id']}"),
        ]]),
    )
    return WAIT_LEAVE_CONFIRM


async def judgeleave_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    allowed, _ = await asyncio.to_thread(_authorized, update.effective_user.id)
    if not allowed:
        await query.answer("Not authorized", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    try:
        _, action, raw_id = query.data.split(":", 2)
        batch_id = int(raw_id)
        result = await asyncio.to_thread(
            decide_batch,
            batch_id,
            f"telegram:{update.effective_user.id}",
            action == "confirm",
        )
    except Exception as exc:
        await query.edit_message_text(f"❌ Judge-leave decision failed safely:\n{exc}")
        return ConversationHandler.END
    if action == "confirm":
        count = sum(int(row.get("case_count") or 0) for row in result.get("entries") or [])
        text = (
            "✅ JUDGE LEAVE CONFIRMED\n\n"
            f"📅 {result['target_date']:%d-%m-%Y}\n"
            f"👨‍⚖️ Judges: {len(result.get('entries') or [])}\n"
            f"📁 Matters excluded from file selection: {count}\n\n"
            "The original case dates and Advocate Diaries records remain unchanged. "
            "The 30-minute-prior alert will be sent automatically."
        )
    else:
        text = "✅ Judge-leave import cancelled. No matter was excluded."
    await query.edit_message_text(text)
    context.user_data.pop("judge_leave_batch", None)
    return ConversationHandler.END


async def judgeleave_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("judge_leave_target", None)
    context.user_data.pop("judge_leave_batch", None)
    await update.effective_message.reply_text("✅ Judge-leave intake cancelled.")
    return ConversationHandler.END


def build_judge_leave_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("judgeleave", judgeleave)],
        states={
            WAIT_LEAVE_PDF: [MessageHandler(filters.Document.PDF, judgeleave_pdf)],
            WAIT_LEAVE_CONFIRM: [
                CallbackQueryHandler(judgeleave_decision, pattern=r"^jl:(?:confirm|cancel):\d+$")
            ],
        },
        fallbacks=[CommandHandler("cancel", judgeleave_cancel)],
        allow_reentry=True,
    )


async def _send_telegram_private(context, text: str, staff_names=None):
    from services.staff_notification_delivery import staff_recipients

    destinations = set(configured_admin_ids())
    for staff in await asyncio.to_thread(staff_recipients, staff_names):
        if staff.get("telegram_user_id"):
            destinations.add(int(staff["telegram_user_id"]))
    for chat_id in destinations:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text[:4000])
        except Exception:
            logger.exception("Private calendar notification failed for Telegram %s", chat_id)


async def _send_leave_alert(context, target, text):
    from services.staff_notification_delivery import deliver_staff_whatsapp
    from services.whatsapp_cloud import send_text_message
    from services.whatsapp_owner_companion import linked_owner_phone

    await _send_telegram_private(context, text, ALERT_STAFF_NAMES)
    await asyncio.to_thread(
        deliver_staff_whatsapp,
        text,
        f"judge-leave:{target.isoformat()}",
        ALERT_STAFF_NAMES,
        datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=IST),
    )
    try:
        owner_phone = await asyncio.to_thread(linked_owner_phone)
        if owner_phone:
            await asyncio.to_thread(send_text_message, owner_phone, text)
    except Exception:
        logger.exception("Owner WhatsApp judge-leave alert could not be delivered in the free window")


async def _send_holiday_greeting(context, target, text):
    from services.staff_notification_delivery import deliver_staff_whatsapp, staff_recipients

    await _send_telegram_private(context, text, None)
    await asyncio.to_thread(
        deliver_staff_whatsapp,
        text,
        f"holiday-greeting:{target.isoformat()}:{text.splitlines()[0]}",
        None,
        datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=IST),
    )


async def _calendar_alert_run(context, plan):
    if not plan:
        return
    target = plan.target_date
    leave_text = await asyncio.to_thread(leave_alert_text, target)
    if leave_text and not await asyncio.to_thread(notification_was_sent, target, "JUDGE_LEAVE"):
        await _send_leave_alert(context, target, leave_text)
        await asyncio.to_thread(mark_notification_sent, target, "JUDGE_LEAVE", leave_text)
    if await asyncio.to_thread(notification_was_sent, target, "HOLIDAY_GREETING"):
        return
    try:
        groups, _ = await asyncio.to_thread(fetch_advocate_diaries_cause_groups, target)
        active, _, _ = await asyncio.to_thread(apply_leave_exclusions, groups, target)
        count = sum(len(group.get("cases") or []) for group in active)
        greeting = await asyncio.to_thread(holiday_greeting, target, count)
        if greeting:
            await _send_holiday_greeting(context, target, greeting)
            await asyncio.to_thread(mark_notification_sent, target, "HOLIDAY_GREETING", greeting)
    except Exception:
        logger.exception("Holiday greeting safety check failed; no greeting was sent")


async def judge_leave_pre_file_job(context):
    await _calendar_alert_run(context, scheduled_evening_plan(datetime.now(IST).date()))


async def saturday_judge_leave_pre_file_job(context):
    await _calendar_alert_run(context, working_saturday_monday_plan(datetime.now(IST).date()))
