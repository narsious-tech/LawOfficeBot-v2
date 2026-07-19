"""Reply-keyboard routing for LawOfficeBot v3 Sprint 1A."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from commands.home import home
from navigation.keyboard import build_main_menu_keyboard
from navigation.menu import route_for_label
from navigation.permissions import can_access_route, resolve_user_role

AsyncHandler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


async def _placeholder(update: Update, title: str, note: str) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        f"{title}\n\n{note}",
        reply_markup=build_main_menu_keyboard(),
    )


async def route_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command_callbacks: Mapping[str, AsyncHandler] | None = None,
) -> None:
    """Route a main-menu button to an existing command or safe placeholder."""
    message = update.effective_message
    if not message or not message.text:
        return

    route = route_for_label(message.text)
    if not route:
        return

    if route == "dashboard":
        await home(update, context)
        return

    role = resolve_user_role(
        update.effective_user.id if update.effective_user else None
    )
    if not can_access_route(role, route):
        await _placeholder(
            update,
            "🔒 Restricted",
            "This section is available to the administrator.",
        )
        return

    callbacks = command_callbacks or {}
    callback = callbacks.get(route)
    if callback:
        await callback(update, context)
        return

    placeholders = {
        "cases": (
            "📁 Cases",
            "Use /findcase to search the synchronized Advocate Diaries cases. "
            "Universal menu search will be added in the Cases sprint.",
        ),
        "hearings": (
            "📅 Hearings",
            "Use /todayhearings or /tomorrowcause. The full hearing menu is "
            "scheduled for the Hearings sprint.",
        ),
        "appointments": (
            "📆 Appointments",
            "The Advocate Diaries appointment integration will be delivered "
            "in the Appointments sprint.",
        ),
        "staff": (
            "👥 Staff",
            "Use /linkedstaff, /linkstaff or /delinkstaff where available.",
        ),
        "reports": (
            "📊 Reports",
            "Use the existing attendance, task and office report commands. "
            "A consolidated reports menu will follow.",
        ),
        "ai": (
            "🤖 AI Assistant",
            "The AI assistant is reserved for a later sprint.",
        ),
        "settings": (
            "⚙️ Settings",
            "Administrative settings will be connected after the navigation "
            "foundation is verified.",
        ),
    }
    title, note = placeholders.get(
        route,
        ("Law Office Bot", "This module is being connected."),
    )
    await _placeholder(update, title, note)
