"""Telegram pre-handler gate for the centralized Law Office access policy."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from services.access_policy import (
    ROLE_RANK,
    AccessIdentity,
    can_complete_case_work,
    required_level_for_callback,
    required_level_for_command,
    resolve_identity,
)


async def _deny(update: Update, identity: AccessIdentity, required: str) -> None:
    if identity.level == "unlinked":
        text = (
            "🔒 This Law Office command is available only to linked staff.\n\n"
            "Use /linkstaff in a private chat with the bot, or ask Ajay/Priya to "
            "link your Telegram account."
        )
    elif required == "admin":
        text = "⛔ This action is restricted to Ajay/the configured administrator."
    else:
        text = "⛔ This office-wide action is restricted to Ajay and Priya."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(text)


async def command_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    command = message.text.split(maxsplit=1)[0] if message and message.text else ""
    normalized = command.strip().lower().lstrip("/").split("@", 1)[0]
    if (
        normalized == "linkstaff"
        and update.effective_chat
        and update.effective_chat.type != "private"
    ):
        if message:
            await message.reply_text(
                "🔐 For security, /linkstaff can be used only in a private chat "
                "with the bot. Do not post Advocate Diaries credentials in the "
                "office group."
            )
        raise ApplicationHandlerStop
    required = required_level_for_command(command)
    identity = resolve_identity(
        update.effective_user.id if update.effective_user else None
    )
    if ROLE_RANK[identity.level] < ROLE_RANK[required]:
        await _deny(update, identity, required)
        raise ApplicationHandlerStop


async def callback_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if not query:
        return
    required = required_level_for_callback(query.data or "")
    identity = resolve_identity(
        update.effective_user.id if update.effective_user else None
    )
    if (
        (query.data or "").startswith("s13:complete:")
        and identity.level == "staff"
    ):
        try:
            work_id = int((query.data or "").split(":", 2)[2])
            allowed = await asyncio.to_thread(
                can_complete_case_work,
                update.effective_user.id,
                work_id,
            )
        except Exception:
            allowed = False
        if not allowed:
            await query.answer(
                "⛔ You may complete only pending Works assigned to you.",
                show_alert=True,
            )
            raise ApplicationHandlerStop
    if ROLE_RANK[identity.level] < ROLE_RANK[required]:
        await _deny(update, identity, required)
        raise ApplicationHandlerStop


def register_access_control(app) -> None:
    app.add_handler(
        MessageHandler(filters.COMMAND, command_access_gate), group=-100
    )
    app.add_handler(
        CallbackQueryHandler(callback_access_gate), group=-100
    )
