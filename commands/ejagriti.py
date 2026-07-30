"""Admin-only e-Jagriti consumer commission operations desk."""
from __future__ import annotations

import html
import os
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from services.ejagriti_service import (
    dashboard_counts,
    link_case,
    pending_reviews,
    record_snapshot,
    review_decision,
    save_order_record,
)


def _admin_ids() -> set[int]:
    result: set[int] = set()
    for name in ("ADMIN_USER_ID", "AI_ADMIN_USER_IDS", "ADMIN_CHAT_ID"):
        for token in os.getenv(name, "").split(","):
            if token.strip().lstrip("-").isdigit():
                result.add(int(token.strip()))
    return result


async def _authorized(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if user_id not in _admin_ids():
        if update.effective_message:
            await update.effective_message.reply_text(
                "🔒 e-Jagriti review is restricted to the administrator."
            )
        return False
    return True


def _parse_date(value: str):
    value = value.strip()
    if value.lower() in {"", "-", "none"}:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {value}. Use DD-MM-YYYY.")


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Pending Reviews", callback_data="ejg:reviews")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="ejg:home")],
    ])


async def ejagriti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    counts = dashboard_counts()
    text = (
        "⚖️ <b>e-JAGRITI CONSUMER CASE DESK</b>\n\n"
        f"🔗 Linked consumer cases: <b>{counts['linked']}</b>\n"
        f"🛡 Pending date reviews: <b>{counts['pending']}</b>\n"
        f"📄 Consumer orders stored: <b>{counts['orders']}</b>\n\n"
        "CAPTCHA and official case inspection remain manual. The bot stores only "
        "administrator-verified details and changes Office OS only after approval.\n\n"
        "<b>Commands</b>\n"
        "<code>/ejagritilink CASE | FILING_REF | FULL_CASE_NO | COMMISSION</code>\n"
        "<code>/ejagritiupdate CASE | LAST_DATE | NEXT_DATE | PURPOSE | STAGE | HISTORY_COUNT</code>\n"
        "<code>/ejagritireview</code>\n"
        "Reply to an order PDF with:\n"
        "<code>/ejagritiorder CASE | ORDER_DATE</code>"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_keyboard()
    )


async def ejagritilink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    parts = [part.strip() for part in " ".join(context.args).split("|")]
    if len(parts) != 4:
        await update.effective_message.reply_text(
            "Use:\n/ejagritilink CASE | FILING_REF | FULL_CASE_NO | COMMISSION"
        )
        return
    try:
        case = link_case(*parts, update.effective_user.id)
        await update.effective_message.reply_text(
            f"✅ e-Jagriti link saved.\n\nCase: {case['number']}\n"
            f"Title: {case.get('title') or '-'}\nFiling reference: {parts[1]}\n"
            f"e-Jagriti case: {parts[2]}\nCommission: {parts[3]}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Link could not be saved safely: {type(exc).__name__}: {exc}"
        )


async def ejagritiupdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    parts = [part.strip() for part in " ".join(context.args).split("|")]
    if len(parts) < 5:
        await update.effective_message.reply_text(
            "Use:\n/ejagritiupdate CASE | LAST_DATE | NEXT_DATE | PURPOSE | STAGE | HISTORY_COUNT"
        )
        return
    try:
        count = int(parts[5]) if len(parts) > 5 and parts[5] not in {"", "-"} else None
        result = record_snapshot(
            parts[0],
            _parse_date(parts[1]),
            _parse_date(parts[2]),
            parts[3],
            parts[4],
            count,
            update.effective_user.id,
        )
        review = result["review"]
        await update.effective_message.reply_text(
            "✅ <b>VERIFIED e-JAGRITI SNAPSHOT SAVED</b>\n\n"
            f"Case: <b>{html.escape(str(result['case']['number']))}</b>\n"
            f"Office next date: {review.get('local_next_date') or '-'}\n"
            f"e-Jagriti next date: {review.get('ejagriti_next_date') or '-'}\n"
            f"Purpose: {html.escape(parts[3] or '-')}\n"
            f"Stage: {html.escape(parts[4] or '-')}\n\n"
            "The comparison is waiting for your decision.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛡 Review", callback_data="ejg:reviews")
            ]]),
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Snapshot could not be saved safely: {type(exc).__name__}: {exc}"
        )


def _review_text(item: dict) -> str:
    return (
        "⚠️ <b>e-JAGRITI DATE REVIEW — ADMIN DECISION</b>\n\n"
        f"Case: <b>{html.escape(str(item.get('case_number') or '-'))}</b>\n"
        f"Title: {html.escape(str(item.get('case_title') or '-'))}\n"
        f"e-Jagriti: {html.escape(str(item.get('ejagriti_case_number') or '-'))}\n"
        f"Commission: {html.escape(str(item.get('commission') or '-'))}\n\n"
        f"👥 Office next date: <b>{item.get('local_next_date') or '-'}</b>\n"
        f"⚖️ e-Jagriti next date: <b>{item.get('ejagriti_next_date') or '-'}</b>\n"
        f"📝 Purpose: {html.escape(str(item.get('purpose') or '-'))}\n\n"
        "Accepting updates Office OS only. Advocate Diaries is deliberately not "
        "changed automatically by this consumer-case bridge."
    )


async def _send_next_review(message):
    rows = pending_reviews(1)
    if not rows:
        await message.reply_text("✅ No e-Jagriti consumer date review awaits approval.")
        return
    item = rows[0]
    await message.reply_text(
        _review_text(item),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Accept e-Jagriti", callback_data=f"ejg:accept:{item['id']}"
                ),
                InlineKeyboardButton(
                    "👥 Keep Office Date", callback_data=f"ejg:keep:{item['id']}"
                ),
            ],
            [InlineKeyboardButton(
                "⏳ Review Later", callback_data=f"ejg:later:{item['id']}"
            )],
        ]),
    )


async def ejagritireview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _authorized(update):
        await _send_next_review(update.effective_message)


async def ejagriti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    if data[1] == "home":
        counts = dashboard_counts()
        await query.message.reply_text(
            f"⚖️ e-Jagriti Desk\nLinked: {counts['linked']}\n"
            f"Pending reviews: {counts['pending']}\nOrders: {counts['orders']}",
            reply_markup=_keyboard(),
        )
        return
    if data[1] == "reviews":
        await _send_next_review(query.message)
        return
    try:
        decision = {
            "accept": "ACCEPT_EJAGRITI",
            "keep": "KEEP_LOCAL",
            "later": "LATER",
        }[data[1]]
        item = review_decision(int(data[2]), decision, update.effective_user.id)
        await query.edit_message_text(
            f"✅ e-Jagriti decision recorded: {item['review_status']}\n"
            f"{item.get('decision_note') or ''}"
        )
        if decision != "LATER":
            await _send_next_review(query.message)
    except Exception as exc:
        await query.message.reply_text(
            f"❌ Review failed safely: {type(exc).__name__}: {exc}"
        )


async def ejagritiorder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    replied = update.effective_message.reply_to_message
    document = replied.document if replied else None
    if not document or (
        document.mime_type and document.mime_type != "application/pdf"
    ):
        await update.effective_message.reply_text(
            "Reply to the consumer order PDF with:\n"
            "/ejagritiorder CASE | ORDER_DATE"
        )
        return
    parts = [part.strip() for part in " ".join(context.args).split("|")]
    if not parts or not parts[0]:
        await update.effective_message.reply_text(
            "Use: /ejagritiorder CASE | ORDER_DATE"
        )
        return
    temp_path = None
    try:
        case_search = parts[0]
        order_date = _parse_date(parts[1]) if len(parts) > 1 else None
        suffix = Path(document.file_name or "consumer-order.pdf").suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(temp_path)

        from googleapiclient.http import MediaFileUpload
        from services.ejagriti_service import resolve_case
        from utils.drive import (
            get_drive_service,
            get_or_create_case_folder,
            get_or_create_subfolder,
        )

        case = resolve_case(case_search)
        case_folder, _ = get_or_create_case_folder(str(case["number"]))
        order_folder, _ = get_or_create_subfolder(case_folder, "Consumer Orders")
        drive = get_drive_service()
        if not drive:
            raise RuntimeError("Google Drive is not connected.")
        uploaded = drive.files().create(
            body={
                "name": document.file_name
                or f"eJagriti-order-{order_date or 'undated'}.pdf",
                "parents": [order_folder],
            },
            media_body=MediaFileUpload(temp_path, mimetype="application/pdf"),
            fields="id,webViewLink",
        ).execute()
        save_order_record(
            case_search,
            order_date,
            document.file_id,
            document.file_name or "consumer-order.pdf",
            uploaded.get("id"),
            uploaded.get("webViewLink"),
            update.effective_user.id,
        )
        await update.effective_message.reply_text(
            f"✅ Consumer order saved.\nCase: {case['number']}\n"
            f"Order date: {order_date or '-'}\n"
            f"Drive: {uploaded.get('webViewLink') or '-'}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Consumer order could not be stored safely: "
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def register_ejagriti_handlers(app) -> None:
    app.add_handler(CommandHandler("ejagriti", ejagriti))
    app.add_handler(CommandHandler("ejagritilink", ejagritilink))
    app.add_handler(CommandHandler("ejagritiupdate", ejagritiupdate))
    app.add_handler(CommandHandler("ejagritireview", ejagritireview))
    app.add_handler(CommandHandler("ejagritiorder", ejagritiorder))
    app.add_handler(CallbackQueryHandler(ejagriti_callback, pattern=r"^ejg:"))
