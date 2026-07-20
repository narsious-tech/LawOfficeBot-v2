"""Sprint 19: button-driven Law Office Operating System.

Provides a small set of memorable entry points and routes existing production
commands through inline buttons. Existing commands remain available as fallbacks.
"""
from __future__ import annotations

import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

IST = ZoneInfo("Asia/Kolkata")


def _home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌅 Morning", callback_data="los:morning"),
         InlineKeyboardButton("🌆 Evening", callback_data="los:evening")],
        [InlineKeyboardButton("📁 Physical Files", callback_data="los:files"),
         InlineKeyboardButton("⚖️ Hearings", callback_data="los:hearings")],
        [InlineKeyboardButton("📋 My Work", callback_data="los:mywork"),
         InlineKeyboardButton("🏢 Office Status", callback_data="los:status")],
        [InlineKeyboardButton("🔎 Find Case", callback_data="los:findcase"),
         InlineKeyboardButton("ℹ️ Help", callback_data="los:help")],
    ])


def _files_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌆 Select Tomorrow's Files", callback_data="los:evening")],
        [InlineKeyboardButton("📦 My File Status", callback_data="los:myfiles"),
         InlineKeyboardButton("📊 Readiness", callback_data="los:readiness")],
        [InlineKeyboardButton("📄 Official Cause List", callback_data="los:pdf")],
        [InlineKeyboardButton("⬅️ Home", callback_data="los:home")],
    ])


def _hearings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Today's Hearings", callback_data="los:todayhearings")],
        [InlineKeyboardButton("🔴 Live Hearings", callback_data="los:livehearings")],
        [InlineKeyboardButton("⚠️ Pending Next Dates", callback_data="los:pending")],
        [InlineKeyboardButton("⬅️ Home", callback_data="los:home")],
    ])


def _work_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 My Dashboard", callback_data="los:mydashboard")],
        [InlineKeyboardButton("📋 My Works", callback_data="los:myworks"),
         InlineKeyboardButton("✅ My Tasks", callback_data="los:mytasks")],
        [InlineKeyboardButton("⬅️ Home", callback_data="los:home")],
    ])


def _supervisor_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 Office Status", callback_data="los:status")],
        [InlineKeyboardButton("🌆 Evening Dashboard", callback_data="los:evening")],
        [InlineKeyboardButton("📊 Hearing Readiness", callback_data="los:readiness")],
        [InlineKeyboardButton("⚠️ Pending Updates", callback_data="los:pending")],
        [InlineKeyboardButton("⬅️ Home", callback_data="los:home")],
    ])


def _greeting() -> str:
    hour = datetime.now(IST).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


async def office(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if not message:
        return
    name = html.escape((update.effective_user.first_name if update.effective_user else "Team Member") or "Team Member")
    await message.reply_text(
        "🏛 <b>LAW OFFICE OF AJAY CHAWLA</b>\n\n"
        f"{_greeting()}, <b>{name}</b>.\n\n"
        "Use the buttons below. Staff do not need to remember individual commands.",
        parse_mode=ParseMode.HTML,
        reply_markup=_home_keyboard(),
    )


async def mywork_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text("📋 <b>MY WORKSPACE</b>\n\nChoose an option.", parse_mode=ParseMode.HTML, reply_markup=_work_keyboard())


async def files_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text("📁 <b>PHYSICAL FILES</b>\n\nChoose an option.", parse_mode=ParseMode.HTML, reply_markup=_files_keyboard())


async def supervisor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text("👥 <b>SUPERVISOR CENTRE</b>\n\nChoose an office-control option.", parse_mode=ParseMode.HTML, reply_markup=_supervisor_keyboard())


async def los_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]

    if action == "home":
        await query.message.reply_text("🏛 <b>LAW OFFICE CONTROL CENTRE</b>", parse_mode=ParseMode.HTML, reply_markup=_home_keyboard())
        return
    if action == "files":
        await query.message.reply_text("📁 <b>PHYSICAL FILES</b>", parse_mode=ParseMode.HTML, reply_markup=_files_keyboard())
        return
    if action == "hearings":
        await query.message.reply_text("⚖️ <b>HEARINGS</b>", parse_mode=ParseMode.HTML, reply_markup=_hearings_keyboard())
        return
    if action == "mywork":
        await query.message.reply_text("📋 <b>MY WORKSPACE</b>", parse_mode=ParseMode.HTML, reply_markup=_work_keyboard())
        return
    if action == "help":
        await query.message.reply_text(
            "ℹ️ <b>HOW TO USE THE BOT</b>\n\n"
            "Type <code>/start</code> or <code>/office</code>, then use buttons.\n\n"
            "Main entry points:\n"
            "• /office — office control centre\n"
            "• /mywork — personal workspace\n"
            "• /files — physical files\n"
            "• /supervisor — supervision centre",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Home", callback_data="los:home")]]),
        )
        return
    if action == "findcase":
        await query.message.reply_text("🔎 Type <code>/findcase</code> followed by the case number, party name or client name.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Home", callback_data="los:home")]]))
        return

    # Import lazily to avoid circular imports during bot startup.
    if action == "morning":
        from commands.dashboard import morningdashboard
        await morningdashboard(update, context)
    elif action == "evening":
        from commands.evening_dashboard import eveningdashboard
        await eveningdashboard(update, context)
    elif action == "myfiles":
        from commands.role_intelligence import myfilesstatus
        await myfilesstatus(update, context)
    elif action == "readiness":
        from commands.hearing_readiness import readiness
        await readiness(update, context)
    elif action == "pdf":
        from commands.evening_dashboard import printablecauselist
        original_args = context.args
        context.args = ["tomorrow"]
        try:
            await printablecauselist(update, context)
        finally:
            context.args = original_args
    elif action == "todayhearings":
        from bot import todayhearings
        await todayhearings(update, context)
    elif action == "livehearings":
        from commands.live_hearings import livehearings
        await livehearings(update, context)
    elif action == "pending":
        from bot import pendingcases
        await pendingcases(update, context)
    elif action == "mydashboard":
        from commands.role_intelligence import mydashboard
        await mydashboard(update, context)
    elif action == "myworks":
        from commands.workspace_v13 import myworks
        await myworks(update, context)
    elif action == "mytasks":
        from commands.works import mytasks
        await mytasks(update, context)
    elif action == "status":
        from commands.role_intelligence import officestatus
        await officestatus(update, context)
