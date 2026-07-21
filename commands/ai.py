from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from ai.config import AIConfig
from ai.gateway import AIGateway, AIUnavailable
from ai.knowledge_service import OfficeKnowledgeService
from ai.permissions import is_ai_authorized
from ai.schema import ensure_ai_schema
from ai.session_store import AISessionStore

logger = logging.getLogger(__name__)
AI_WAITING_QUESTION = 9201


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Ask Ajay AI", callback_data="ajayai:ask")],
        [InlineKeyboardButton("📄 Case Intelligence", callback_data="ajayai:case")],
        [InlineKeyboardButton("⚖️ Hearing Intelligence", callback_data="ajayai:coming:hearing")],
        [InlineKeyboardButton("📚 Legal Research", callback_data="ajayai:coming:research")],
        [InlineKeyboardButton("📝 Drafting", callback_data="ajayai:coming:drafting")],
        [InlineKeyboardButton("📂 Documents", callback_data="ajayai:coming:documents")],
        [InlineKeyboardButton("❌ Close", callback_data="ajayai:close")],
    ])


async def _authorized(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if not is_ai_authorized(user_id):
        await update.effective_message.reply_text("🔒 Ajay AI is private and access is not enabled for this account.")
        return False
    return True


async def ai_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return ConversationHandler.END
    cfg = AIConfig.from_env()
    status = "READY" if cfg.enabled and cfg.api_key else "CONFIGURATION REQUIRED"
    await update.effective_message.reply_text(
        "🧠 AJAY AI\n\n"
        f"Status: {status}\n"
        "Private legal intelligence workspace.\n\n"
        "The first working capability is Ask Ajay AI. Case Intelligence can use a bounded local case snapshot.",
        reply_markup=_menu(),
    )
    return ConversationHandler.END


async def ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_ai_authorized(query.from_user.id):
        await query.message.reply_text("🔒 Access denied.")
        return ConversationHandler.END
    data = query.data or ""
    if data == "ajayai:close":
        await query.edit_message_text("Ajay AI workspace closed. Use /ai to reopen it.")
        return ConversationHandler.END
    if data.startswith("ajayai:coming:"):
        feature = data.rsplit(":", 1)[-1].replace("_", " ").title()
        await query.message.reply_text(f"🧠 {feature} is reserved for the next intelligence release.")
        return ConversationHandler.END
    mode = "case" if data == "ajayai:case" else "general"
    context.user_data["ajay_ai_mode"] = mode
    prompt = (
        "Send the case number, client name, or party name. Ajay AI will load a limited local case snapshot and prepare a structured working note."
        if mode == "case" else
        "Send your legal or office-work question. Do not include passwords, API keys, or unnecessary sensitive personal data."
    )
    await query.message.reply_text(prompt + "\n\nUse /cancelai to stop.")
    return AI_WAITING_QUESTION


def _split(text: str, size: int = 3800) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


async def ai_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Please send a text request.")
        return AI_WAITING_QUESTION
    await update.effective_message.reply_text("🧠 Preparing a reviewable AI working note…")
    user_id = update.effective_user.id
    mode = context.user_data.get("ajay_ai_mode", "general")
    try:
        await asyncio.to_thread(ensure_ai_schema)
        store = AISessionStore()
        session_id = await asyncio.to_thread(store.create_session, user_id, mode, text if mode == "case" else None)
        office_context = None
        ai_request = text
        if mode == "case":
            office_context = await asyncio.to_thread(OfficeKnowledgeService().case_snapshot, text)
            ai_request = "Prepare a preliminary case-intelligence brief from the verified office context."
        await asyncio.to_thread(store.add_message, session_id, "user", text)
        result = await asyncio.to_thread(
            AIGateway(store=store).generate,
            user_id=user_id,
            session_id=session_id,
            user_text=ai_request,
            feature="general",
            office_context=office_context,
        )
        await asyncio.to_thread(store.add_message, session_id, "assistant", result.text)
        for chunk in _split(result.text):
            await update.effective_message.reply_text(chunk)
        await update.effective_message.reply_text("⚠️ Review and verify before relying on this working note.", reply_markup=_menu())
    except AIUnavailable as exc:
        await update.effective_message.reply_text(f"⚠️ Ajay AI unavailable\n\n{exc}")
    except Exception as exc:
        logger.exception("Ajay AI request failed")
        await update.effective_message.reply_text(f"❌ Ajay AI failed safely: {type(exc).__name__}")
    finally:
        context.user_data.pop("ajay_ai_mode", None)
    return ConversationHandler.END


async def cancel_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ajay_ai_mode", None)
    await update.effective_message.reply_text("Ajay AI request cancelled.")
    return ConversationHandler.END


def build_ai_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("ai", ai_home),
            CallbackQueryHandler(ai_callback, pattern=r"^ajayai:"),
        ],
        states={
            AI_WAITING_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ai_question)],
        },
        fallbacks=[CommandHandler("cancelai", cancel_ai)],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )
