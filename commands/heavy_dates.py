"""Telegram command for the hearing-load forecast."""
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from services.heavy_dates_service import (
    get_heavy_dates, heavy_date_settings, parse_hearing_date,
    render_heavy_date_detail, render_heavy_dates,
)


async def _send_chunks(message, text):
    lines = text.splitlines()
    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > 3900 and chunk:
            await message.reply_text(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await message.reply_text(chunk)


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
    await _send_chunks(update.effective_message, message)


async def heavydate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /heavydate DD-MM-YYYY\nExample: /heavydate 04-11-2026"
        )
        return
    target = parse_hearing_date(context.args[0])
    if not target:
        await update.effective_message.reply_text("❌ Invalid date. Use DD-MM-YYYY.")
        return
    try:
        settings = heavy_date_settings()
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        days = max(settings["lookahead_days"], (target - today).days + 1)
        items = get_heavy_dates(lookahead_days=max(1, min(365, days)))
        item = next((entry for entry in items if entry["date"] == target), None)
        text = render_heavy_date_detail(item)
    except Exception as exc:
        text = f"❌ Heavy-date detail failed safely:\n{type(exc).__name__}: {exc}"
    await _send_chunks(update.effective_message, text)

