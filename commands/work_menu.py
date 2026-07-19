"""Menu-driven Advocate Diaries work navigation."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from commands.works import works
from navigation.keyboard import build_main_menu_keyboard


def work_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Pending Works", callback_data="workmenu:pending"),
            InlineKeyboardButton("✅ Completed Works", callback_data="workmenu:completed"),
        ],
        [
            InlineKeyboardButton("👤 My Tasks", callback_data="workmenu:mytasks"),
            InlineKeyboardButton("🔄 Refresh", callback_data="workmenu:refresh"),
        ],
        [InlineKeyboardButton("ℹ️ Commands", callback_data="workmenu:help")],
    ])


async def work_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "📋 WORKS & TASKS\n\n"
        "Advocate Diaries remains the source of truth for Works. "
        "The bot creates linked internal Tasks only when an administrator uses /assignwork.\n\n"
        "Select an option:",
        reply_markup=work_menu_keyboard(),
    )


async def work_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = (query.data or "").partition(":")[2]

    if action in {"pending", "completed", "refresh"}:
        old_args = list(context.args or [])
        context.args = ["completed" if action == "completed" else "pending"]
        try:
            await works(update, context)
        finally:
            context.args = old_args
        return

    if action == "mytasks":
        from commands.works import mytasks
        await mytasks(update, context)
        return

    if action == "help":
        await query.message.reply_text(
            "WORK–TASK COMMANDS\n\n"
            "/works pending — pending Advocate Diaries Works\n"
            "/works completed — completed Works\n"
            "/work NUMBER — full Work details\n"
            "/assignwork STAFF NUMBER... — create linked staff Task(s)\n"
            "/completework NUMBER — complete Advocate Diaries Work directly\n"
            "/mytasks — staff member's pending Tasks\n"
            "/completetask TASK_ID — complete linked Task and its AD Work\n"
            "/taskdetails TASK_ID — full Task details\n\n"
            "No command in this menu creates a new Advocate Diaries Work.",
            reply_markup=build_main_menu_keyboard(),
        )
