from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from commands.dashboard import fetch_advocate_diaries_cause_groups
from services.printable_causelist_service import (
    PdfDependencyUnavailable,
    build_causelist_pdf,
)

logger = logging.getLogger(__name__)


def _blackouts(groups):
    values = []
    for group in groups:
        for key in ("blackout_dates", "blackouts"):
            value = group.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value if item)
    return list(dict.fromkeys(values))


def _text_causelist(groups, target_date, source):
    lines = [
        "⚖ PRINTABLE CAUSE LIST (TEXT FALLBACK)",
        f"📅 {target_date.strftime('%d %b %Y')}",
        f"Source: Advocate Diaries {source}",
        "",
    ]
    serial = 1
    for group in groups:
        judge = str(group.get("judge_name") or "Judge not recorded").strip()
        court = str(group.get("court_name") or "Court not recorded").strip()
        floor = str(group.get("floor") or "-").strip()
        room = str(group.get("room") or "-").strip()
        lines.append(f"{judge} ({court}) | Floor {floor} | Room {room}")
        for case in group.get("cases") or []:
            number = str(case.get("case_number") or "-").strip()
            title = str(case.get("case_title") or "Title not recorded").strip()
            stage = str(case.get("stage") or "Purpose not recorded").strip()
            lines.append(f"{serial}. {number} - {title} - {stage}")
            serial += 1
        lines.append("")
    return "\n".join(lines).strip()


async def _pdf_for(target_date):
    groups, source = await asyncio.to_thread(
        fetch_advocate_diaries_cause_groups, target_date
    )
    out = os.path.join(
        tempfile.gettempdir(), f"Cause_List_{target_date.isoformat()}.pdf"
    )
    try:
        await asyncio.to_thread(
            build_causelist_pdf, groups, target_date, _blackouts(groups), out
        )
    except PdfDependencyUnavailable:
        logger.exception("ReportLab unavailable; using cause-list text fallback")
        return groups, source, None
    return groups, source, out


async def printablecauselist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    target = now.date()
    if context.args and context.args[0].lower() in ("tomorrow", "tom"):
        target += timedelta(days=1)

    groups, source, path = await _pdf_for(target)
    if path:
        with open(path, "rb") as file_handle:
            await update.effective_message.reply_document(
                file_handle,
                filename=os.path.basename(path),
                caption=(
                    f"Printable cause list - {target.strftime('%d %b %Y')} - "
                    f"Advocate Diaries {source}"
                ),
            )
        return

    await update.effective_message.reply_text(
        "⚠️ PDF generation is temporarily unavailable. Sending the cause list "
        "as text; install ReportLab through requirements.txt to restore PDF output."
    )
    await update.effective_message.reply_text(
        _text_causelist(groups, target, source)[:4000]
    )


async def eveningdashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_dashboard(context, chat_id=update.effective_chat.id, force=True)


async def send_evening_dashboard(context, chat_id=None, force=False):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    target = now.date() + timedelta(days=1)
    groups, source, path = await _pdf_for(target)
    total = sum(len(group.get("cases") or []) for group in groups)
    pdf_line = (
        "Printable Legal-size cause list attached."
        if path
        else "PDF unavailable; text cause list follows."
    )
    text = (
        "🌆 EVENING OPERATIONS DASHBOARD\n"
        f"📅 Tomorrow: {target.strftime('%d %b %Y')}\n"
        f"⚖ Matters: {total}\n"
        "📁 Mark required physical files from tomorrow's cause list.\n"
        "📋 Preparation priority: urgent, overdue, due today and due tomorrow Works.\n\n"
        f"{pdf_line}"
    )
    raw_destination = (
        chat_id
        or os.getenv("OFFICE_GROUP_CHAT_ID")
        or os.getenv("PHYSICAL_FILE_GROUP_CHAT_ID")
    )
    if not raw_destination:
        raise RuntimeError(
            "OFFICE_GROUP_CHAT_ID or PHYSICAL_FILE_GROUP_CHAT_ID is required"
        )
    destination = int(raw_destination)
    await context.bot.send_message(chat_id=destination, text=text)

    if path:
        with open(path, "rb") as file_handle:
            await context.bot.send_document(
                chat_id=destination,
                document=file_handle,
                filename=os.path.basename(path),
            )
    else:
        await context.bot.send_message(
            chat_id=destination,
            text=_text_causelist(groups, target, source)[:4000],
        )


async def evening_dashboard_job(context):
    await send_evening_dashboard(context)
