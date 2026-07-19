"""Home dashboard command for LawOfficeBot v3 Sprint 1A."""

from __future__ import annotations

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from navigation.keyboard import build_main_menu_keyboard
from services.dashboard_service import (
    display_attendance,
    display_count,
    get_dashboard_summary,
    greeting_for_now,
)


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Team Member"
    return user.first_name or user.full_name or "Team Member"


def build_dashboard_text(update: Update) -> str:
    user = update.effective_user
    summary = get_dashboard_summary(user.id if user else None)
    name = html.escape(_display_name(update))

    return (
        "🏛 <b>LAW OFFICE OF AJAY CHAWLA</b>\n\n"
        f"{greeting_for_now()}, <b>{name}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 Hearings Today        <b>{display_count(summary.hearings_today)}</b>\n"
        f"📆 Appointments Today    <b>{display_count(summary.appointments_today)}</b>\n\n"
        f"📋 Pending Works         <b>{display_count(summary.pending_works)}</b>\n"
        f"✅ Pending Tasks         <b>{display_count(summary.pending_tasks)}</b>\n\n"
        "👥 Staff Present         "
        f"<b>{display_attendance(summary.staff_present, summary.staff_total)}</b>\n\n"
        f"📂 Documents Today       <b>{display_count(summary.documents_today)}</b>\n"
        f"🔔 Notifications         <b>{display_count(summary.notifications)}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select an office module below."
    )


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the persistent office dashboard."""
    del context
    message = update.effective_message
    if not message:
        return

    await message.reply_text(
        build_dashboard_text(update),
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu_keyboard(),
    )


# /start and /home intentionally share the same landing screen.
start = home
