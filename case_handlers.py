"""
Core modular handler registration for LawOfficeBot-v2.

Case creation is intentionally performed only in Advocate Diaries. New cases
are imported automatically through the existing Advocate Diaries sync.
"""

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from commands.find_case import findcase


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "Law Office Bot Live\n\n"
        "New cases must be added in Advocate Diaries and will be "
        "automatically synced here.\n\n"
        "Useful commands:\n"
        "/findcase CASE_ID\n"
        "/attendance\n"
        "/works\n"
        "/commands"
    )


async def newcase_disabled(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "ℹ️ New-case entry is disabled in Telegram.\n\n"
        "Please add the case in Advocate Diaries. The scheduled sync will "
        "automatically import it into the Law Office Bot."
    )


def register_case_handlers(application: Application) -> None:
    """
    Register these handlers before the legacy handlers so they take precedence.
    """
    application.add_handler(
        CommandHandler("start", start),
        group=0,
    )
    application.add_handler(
        CommandHandler("newcase", newcase_disabled),
        group=0,
    )
    application.add_handler(
        CommandHandler("findcase", findcase),
        group=0,
    )
