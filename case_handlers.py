"""
Modular case-handler registration for LawOfficeBot-v2.

Register this before the legacy handlers. Because python-telegram-bot uses the
first matching handler in a handler group, these modular handlers take
precedence while the legacy code remains available as a rollback.
"""

from telegram.ext import Application, CommandHandler

from commands.find_case import findcase
from navigation.registration import register_navigation_handlers

from commands.new_case import (
    build_new_case_conversation_handler,
    start,
)


def register_case_handlers(application: Application) -> None:
    """Register modular start, new-case, and find-case handlers."""
    register_navigation_handlers(application)
    application.add_handler(
        build_new_case_conversation_handler(),
        group=0,
    )
    application.add_handler(
        CommandHandler("start", start),
        group=0,
    )
    application.add_handler(
        CommandHandler("findcase", findcase),
        group=0,
    )
