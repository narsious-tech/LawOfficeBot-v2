"""Telegram command for the hearing-load forecast."""
from telegram import Update
from telegram.ext import ContextTypes

from services.heavy_dates_service import get_heavy_dates, heavy_date_settings, render_heavy_dates


async def heavydates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = heavy_date_settings()
    days = settings["lookahead_days"]
    if context.args:
        try:
            days = max(1, min(365, int(context.args[0])))
        except (TypeError, ValueError):
            await update.effective_message.reply_text("Usage: /heavydates [number of days]\nExample: /heavydates 60")
            return
    try:
        items = get_heavy_dates(lookahead_days=days)
        message = render_heavy_dates(items, lookahead_days=days)
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Heavy-date forecast failed safely:\n{type(exc).__name__}: {exc}"
        )
        return
    for start in range(0, len(message), 3900):
        await update.effective_message.reply_text(message[start:start + 3900])

