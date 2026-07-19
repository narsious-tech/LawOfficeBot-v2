"""Register Sprint 1A navigation without changing existing command modules."""

from __future__ import annotations

import re

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from commands.attendance import attendance
from commands.files import files
from commands.home import home
from commands.work_menu import work_menu, work_menu_callback
from commands.works import mytasks
from navigation.menu import get_main_menu_items
from navigation.router import route_main_menu


def register_navigation_handlers(application: Application) -> None:
    """Register dashboard commands and exact reply-keyboard text routes."""
    callbacks = {
        "works": work_menu,
        "tasks": mytasks,
        "documents": files,
        "attendance": attendance,
    }

    async def menu_router(update, context):
        await route_main_menu(update, context, callbacks)

    labels = [item.label for item in get_main_menu_items()]
    pattern = "^(?:" + "|".join(re.escape(label) for label in labels) + ")$"

    # Group -1 ensures the new home screen wins over legacy duplicate /start
    # handlers, while exact menu-label matching avoids conversation interference.
    application.add_handler(CommandHandler("start", home), group=-1)
    application.add_handler(CommandHandler("home", home), group=-1)
    application.add_handler(CommandHandler("dashboard", home), group=-1)
    application.add_handler(CommandHandler("workmenu", work_menu), group=-1)
    application.add_handler(
        CallbackQueryHandler(work_menu_callback, pattern=r"^workmenu:"),
        group=-1,
    )
    application.add_handler(
        MessageHandler(filters.Regex(pattern), menu_router),
        group=-1,
    )
