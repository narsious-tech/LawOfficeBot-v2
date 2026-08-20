"""Telegram capture and administrator views for the staff activity feed."""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from services.access_policy import resolve_identity
from services.staff_activity_service import (
    activity_feed_enabled,
    admin_activity_chat_id,
    ensure_staff_activity_schema,
    mark_activity_notification,
    message_summary,
    recent_staff_activity,
    record_staff_activity,
    render_admin_notification,
)

logger = logging.getLogger(__name__)


def _button_label(query) -> str:
    data = query.data or ""
    markup = getattr(getattr(query, "message", None), "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            if getattr(button, "callback_data", None) == data:
                return str(getattr(button, "text", None) or data)
    return data


async def _save_and_notify(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           event_kind: str, summary: str, metadata: dict) -> None:
    if not activity_feed_enabled() or not update.effective_user:
        return
    identity = await asyncio.to_thread(resolve_identity, update.effective_user.id)
    if not identity.linked or identity.level == "admin":
        return
    chat = update.effective_chat
    activity_id = await asyncio.to_thread(
        record_staff_activity,
        update_id=update.update_id,
        event_kind=event_kind,
        user_id=update.effective_user.id,
        staff_name=identity.name,
        staff_role=identity.role,
        chat_id=chat.id if chat else None,
        chat_type=chat.type if chat else None,
        chat_title=getattr(chat, "title", None) if chat else None,
        summary=summary,
        metadata=metadata,
    )
    if activity_id is None:  # Telegram redelivery already recorded.
        return
    destination = admin_activity_chat_id()
    if destination is None:
        await asyncio.to_thread(
            mark_activity_notification, activity_id,
            error="ADMIN_USER_ID is not configured",
        )
        return
    alert = render_admin_notification(
        staff_name=identity.name,
        staff_role=identity.role,
        event_kind=event_kind,
        chat_type=chat.type if chat else None,
        chat_title=getattr(chat, "title", None) if chat else None,
        summary=summary,
    )
    try:
        await context.bot.send_message(chat_id=destination, text=alert)
        await asyncio.to_thread(mark_activity_notification, activity_id)
    except Exception as exc:
        await asyncio.to_thread(
            mark_activity_notification, activity_id,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _safe_save_and_notify(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    event_kind: str, summary: str, metadata: dict,
) -> None:
    try:
        await _save_and_notify(
            update, context, event_kind, summary, metadata
        )
    except Exception:
        # Monitoring must never interrupt the staff member's actual command.
        logger.exception("Staff activity capture failed safely")


async def capture_staff_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    summary, metadata = message_summary(update.effective_message)
    context.application.create_task(
        _safe_save_and_notify(update, context, "MESSAGE", summary, metadata),
        update=update,
        name=f"staff-activity-message-{update.update_id}",
    )


async def capture_staff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    label = _button_label(query)
    context.application.create_task(
        _safe_save_and_notify(
            update, context, "BUTTON_ACTION", f"🔘 {label}",
            {"callback_data": (query.data or "")[:500], "button_text": label[:500]},
        ),
        update=update,
        name=f"staff-activity-button-{update.update_id}",
    )


async def activitystatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    destination = admin_activity_chat_id()
    await update.effective_message.reply_text(
        "🔔 STAFF ACTIVITY FEED\n\n"
        f"Status: {'ENABLED' if activity_feed_enabled() else 'DISABLED'}\n"
        f"Private admin destination: {'Configured' if destination else 'Missing ADMIN_USER_ID'}\n"
        "Captures: staff messages, commands, media/location submissions and button actions.\n"
        "Security: credential-bearing /linkstaff content is redacted."
    )


async def activityfeed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = 25
    if context.args and context.args[0].isdigit():
        limit = max(1, min(int(context.args[0]), 100))
    rows = await asyncio.to_thread(recent_staff_activity, limit)
    if not rows:
        await update.effective_message.reply_text("No staff activity has been recorded yet.")
        return
    chunks = ["🔔 RECENT STAFF BOT ACTIVITY\n"]
    for row in rows:
        created = row["created_at"].strftime("%d-%m-%Y %I:%M %p")
        delivery = "✅" if row.get("notified_at") else "⚠️"
        chunks.append(
            f"{delivery} {created}\n👤 {row['staff_name']} · {row['event_kind']}\n"
            f"💬 {row['summary'][:700]}\n"
        )
    text = "\n".join(chunks)
    while text:
        cut = len(text) if len(text) <= 3900 else text.rfind("\n\n", 0, 3900)
        if cut <= 0:
            cut = 3900
        await update.effective_message.reply_text(text[:cut])
        text = text[cut:].lstrip()


def register_staff_activity_handlers(app) -> None:
    ensure_staff_activity_schema()
    # Capture before the access gate so rejected attempts by linked staff are
    # also visible to Ajay. Deduplication protects against Telegram redelivery.
    app.add_handler(MessageHandler(filters.ALL, capture_staff_message), group=-110)
    app.add_handler(CallbackQueryHandler(capture_staff_callback), group=-110)
    app.add_handler(CommandHandler("activitystatus", activitystatus), group=-20)
    app.add_handler(CommandHandler("activityfeed", activityfeed), group=-20)
