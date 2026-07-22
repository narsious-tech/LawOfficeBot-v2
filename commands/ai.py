from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from ai.config import AIConfig
from ai.gateway import AIGateway, AIUnavailable
from ai.knowledge_service import CaseMatch, OfficeKnowledgeService
from ai.permissions import is_ai_authorized
from ai.schema import ensure_ai_schema
from ai.session_store import AISessionStore

logger = logging.getLogger(__name__)
AI_WAITING_QUESTION = 9201


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Ask Ajay AI", callback_data="ajayai:ask")],
        [InlineKeyboardButton("📄 Case Intelligence", callback_data="ajayai:case")],
        [InlineKeyboardButton("⚖️ Hearing Intelligence", callback_data="ajayai:hearing")],
        [InlineKeyboardButton("📚 Legal Research", callback_data="ajayai:coming:research")],
        [InlineKeyboardButton("📝 Drafting", callback_data="ajayai:coming:drafting")],
        [InlineKeyboardButton("📂 Documents", callback_data="ajayai:coming:documents")],
        [InlineKeyboardButton("❌ Close", callback_data="ajayai:close")],
    ])


def _case_buttons(cases: list[CaseMatch]) -> InlineKeyboardMarkup:
    rows = []
    for case in cases:
        label = case.case_number
        if len(label) > 48:
            label = label[:45] + "…"
        rows.append([InlineKeyboardButton(f"⚖️ {label}", callback_data=f"ajayai:casepick:{case.db_id}")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="ajayai:close")])
    return InlineKeyboardMarkup(rows)


def _hearing_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Today", callback_data="ajayai:hearingday:today"),
            InlineKeyboardButton("Tomorrow", callback_data="ajayai:hearingday:tomorrow"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="ajayai:close")],
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
        "Case Intelligence now prepares a grounded brief from one selected office case.",
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
        context.user_data.pop("ajay_ai_mode", None)
        await query.edit_message_text("Ajay AI workspace closed. Use /ai to reopen it.")
        return ConversationHandler.END
    if data.startswith("ajayai:coming:"):
        feature = data.rsplit(":", 1)[-1].replace("_", " ").title()
        await query.message.reply_text(f"🧠 {feature} is reserved for the next intelligence release.")
        return ConversationHandler.END
    if data == "ajayai:hearing":
        await query.message.reply_text(
            "⚖️ HEARING INTELLIGENCE\n\nChoose the hearing day for a grounded preparation brief.",
            reply_markup=_hearing_buttons(),
        )
        return ConversationHandler.END
    if data == "ajayai:case":
        context.user_data["ajay_ai_mode"] = "case_search"
        try:
            cases = await asyncio.to_thread(OfficeKnowledgeService().search_cases, "", 6)
        except Exception:
            logger.exception("Could not load recent cases for Ajay AI")
            cases = []
        text = "📄 CASE INTELLIGENCE\n\nSelect one recent case below, or send a case number, client name, or party name."
        await query.message.reply_text(text, reply_markup=_case_buttons(cases) if cases else None)
        return AI_WAITING_QUESTION
    context.user_data["ajay_ai_mode"] = "general"
    await query.message.reply_text(
        "Send your legal or office-work question. Do not include passwords, API keys, or unnecessary sensitive personal data.\n\nUse /cancelai to stop."
    )
    return AI_WAITING_QUESTION


def _split(text: str, size: int = 3800) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


async def _generate_case_brief(update: Update, context: ContextTypes.DEFAULT_TYPE, case_db_id: int):
    message = update.effective_message
    user_id = update.effective_user.id
    await message.reply_text("🧠 Loading verified office context and preparing the case brief…")
    try:
        await asyncio.to_thread(ensure_ai_schema)
        knowledge = OfficeKnowledgeService()
        case_context = await asyncio.to_thread(knowledge.build_case_context, case_db_id)
        if not case_context:
            await message.reply_text("The selected case could not be found. Please search again from /ai.")
            return ConversationHandler.END
        case_number = str(case_context.case.get("case_number") or case_context.case.get("case_id") or case_db_id)
        store = AISessionStore()
        session_id = await asyncio.to_thread(store.create_session, user_id, "case_intelligence", case_number)
        request = "Prepare the grounded case-intelligence brief for the selected case."
        await asyncio.to_thread(store.add_message, session_id, "user", request)
        result = await asyncio.to_thread(
            AIGateway(store=store).generate,
            user_id=user_id,
            session_id=session_id,
            user_text=request,
            feature="case_intelligence",
            office_context=case_context.to_prompt(),
        )
        await asyncio.to_thread(store.add_message, session_id, "assistant", result.text)
        for chunk in _split(result.text):
            await message.reply_text(chunk)
        unavailable = list(case_context.unavailable_sources)
        if unavailable:
            safe = ", ".join(html.escape(item) for item in unavailable)
            await message.reply_text(f"ℹ️ Data not available to this brief: {safe}.")
        await message.reply_text(
            "⚠️ AI working note: verify facts, documents, orders, and law before relying on it.",
            reply_markup=_menu(),
        )
    except AIUnavailable as exc:
        await message.reply_text(f"⚠️ Ajay AI unavailable\n\n{exc}")
    except Exception as exc:
        logger.exception("Ajay AI case-intelligence request failed")
        await message.reply_text(f"❌ Case Intelligence failed safely: {type(exc).__name__}")
    finally:
        context.user_data.pop("ajay_ai_mode", None)
    return ConversationHandler.END


async def ai_case_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_ai_authorized(query.from_user.id):
        await query.message.reply_text("🔒 Access denied.")
        return ConversationHandler.END
    try:
        case_db_id = int((query.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await query.message.reply_text("Invalid case selection. Please reopen /ai.")
        return ConversationHandler.END
    return await _generate_case_brief(update, context, case_db_id)


async def ai_hearing_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_ai_authorized(query.from_user.id):
        await query.message.reply_text("🔒 Access denied.")
        return ConversationHandler.END
    choice = (query.data or "").rsplit(":", 1)[-1]
    target = date.today()
    try:
        from datetime import datetime
        target = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        pass
    target += timedelta(days=1) if choice == "tomorrow" else timedelta()
    label = "tomorrow" if choice == "tomorrow" else "today"
    await query.message.reply_text(f"🧠 Loading verified office records and preparing {label}'s hearing brief…")
    user_id = query.from_user.id
    try:
        await asyncio.to_thread(ensure_ai_schema)
        hearing_context = await asyncio.to_thread(
            OfficeKnowledgeService().build_hearing_day_context, target, 20
        )
        if not hearing_context.cases:
            await query.message.reply_text(
                f"No hearings were found in the connected master-case records for {target.strftime('%d-%m-%Y')}.\n\n"
                "This does not confirm the Advocate Diaries cause list is empty; check the cause-list module if required.",
                reply_markup=_menu(),
            )
            return ConversationHandler.END
        store = AISessionStore()
        session_id = await asyncio.to_thread(
            store.create_session, user_id, "hearing_intelligence", target.isoformat()
        )
        request = f"Prepare the grounded hearing-intelligence brief for {target.isoformat()}."
        await asyncio.to_thread(store.add_message, session_id, "user", request)
        result = await asyncio.to_thread(
            AIGateway(store=store).generate,
            user_id=user_id,
            session_id=session_id,
            user_text=request,
            feature="hearing_intelligence",
            office_context=hearing_context.to_prompt(),
        )
        await asyncio.to_thread(store.add_message, session_id, "assistant", result.text)
        await query.message.reply_text(
            f"⚖️ HEARING INTELLIGENCE • {target.strftime('%d-%m-%Y')}\n"
            f"Verified master-case matches: {len(hearing_context.cases)}"
        )
        for chunk in _split(result.text):
            await query.message.reply_text(chunk)
        if hearing_context.unavailable_sources:
            safe = ", ".join(html.escape(item) for item in hearing_context.unavailable_sources)
            await query.message.reply_text(f"ℹ️ Data not available to this brief: {safe}.")
        await query.message.reply_text(
            "⚠️ AI working note: verify the cause list, file contents, orders, facts, and law before court.",
            reply_markup=_menu(),
        )
    except AIUnavailable as exc:
        await query.message.reply_text(f"⚠️ Ajay AI unavailable\n\n{exc}")
    except Exception as exc:
        logger.exception("Ajay AI hearing-intelligence request failed")
        await query.message.reply_text(f"❌ Hearing Intelligence failed safely: {type(exc).__name__}")
    return ConversationHandler.END


async def ai_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Please send a text request.")
        return AI_WAITING_QUESTION
    mode = context.user_data.get("ajay_ai_mode", "general")
    if mode == "case_search":
        try:
            cases = await asyncio.to_thread(OfficeKnowledgeService().search_cases, text, 10)
        except Exception as exc:
            logger.exception("Ajay AI case search failed")
            await update.effective_message.reply_text(f"❌ Case search failed safely: {type(exc).__name__}")
            return AI_WAITING_QUESTION
        if not cases:
            await update.effective_message.reply_text("No matching case was found. Try another case number, client, or party name.")
            return AI_WAITING_QUESTION
        await update.effective_message.reply_text(
            f"Select one case for the AI brief ({len(cases)} match{'es' if len(cases) != 1 else ''}):",
            reply_markup=_case_buttons(cases),
        )
        return AI_WAITING_QUESTION

    await update.effective_message.reply_text("🧠 Preparing a reviewable AI working note…")
    user_id = update.effective_user.id
    try:
        await asyncio.to_thread(ensure_ai_schema)
        store = AISessionStore()
        session_id = await asyncio.to_thread(store.create_session, user_id, "general", None)
        await asyncio.to_thread(store.add_message, session_id, "user", text)
        result = await asyncio.to_thread(
            AIGateway(store=store).generate,
            user_id=user_id,
            session_id=session_id,
            user_text=text,
            feature="general",
        )
        await asyncio.to_thread(store.add_message, session_id, "assistant", result.text)
        for chunk in _split(result.text):
            await update.effective_message.reply_text(chunk)
        await update.effective_message.reply_text(
            "⚠️ Review and verify before relying on this working note.", reply_markup=_menu()
        )
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
            CallbackQueryHandler(ai_hearing_day, pattern=r"^ajayai:hearingday:(today|tomorrow)$"),
            CallbackQueryHandler(ai_callback, pattern=r"^ajayai:(?!casepick:).+"),
        ],
        states={
            AI_WAITING_QUESTION: [
                CallbackQueryHandler(ai_case_pick, pattern=r"^ajayai:casepick:\d+$"),
                CallbackQueryHandler(ai_callback, pattern=r"^ajayai:close$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_question),
            ],
        },
        fallbacks=[CommandHandler("cancelai", cancel_ai)],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )
