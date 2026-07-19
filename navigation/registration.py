"""Register LawOfficeBot v3 navigation and menu-driven workspaces."""

from __future__ import annotations

import re

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from commands.attendance import attendance
from commands.case_workspace import (
    casesearch,
    caseworkspace,
    case_workspace_callback,
    case_workspace_menu,
)
from commands.files import files
from commands.home import home
from commands.work_menu import work_menu, work_menu_callback
from commands.works import mytasks
from navigation.menu import get_main_menu_items
from navigation.router import route_main_menu


def register_navigation_handlers(application: Application) -> None:
    """Register dashboard commands and exact reply-keyboard text routes."""
    callbacks = {
        "cases": case_workspace_menu,
        "works": work_menu,
        "tasks": mytasks,
        "documents": files,
        "attendance": attendance,
    }

    async def menu_router(update, context):
        await route_main_menu(update, context, callbacks)

    labels = [item.label for item in get_main_menu_items()]
    pattern = "^(?:" + "|".join(re.escape(label) for label in labels) + ")$"

    application.add_handler(CommandHandler("start", home), group=-1)
    application.add_handler(CommandHandler("home", home), group=-1)
    application.add_handler(CommandHandler("dashboard", home), group=-1)

    application.add_handler(CommandHandler("cases", case_workspace_menu), group=-1)
    application.add_handler(CommandHandler("casesearch", casesearch), group=-1)
    application.add_handler(CommandHandler("caseworkspace", caseworkspace), group=-1)
    application.add_handler(
        CallbackQueryHandler(case_workspace_callback, pattern=r"^casews:"),
        group=-1,
    )

    application.add_handler(CommandHandler("workmenu", work_menu), group=-1)
    application.add_handler(
        CallbackQueryHandler(work_menu_callback, pattern=r"^workmenu:"),
        group=-1,
    )
    application.add_handler(
        MessageHandler(filters.Regex(pattern), menu_router),
        group=-1,
    )
