from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from services.hearing_readiness_service import readiness_summary

IST = ZoneInfo("Asia/Kolkata")


def _target_date(args) -> object:
    today = datetime.now(IST).date()
    if args and args[0].lower() in {"today", "tod"}:
        return today
    return today + timedelta(days=1)


def _summary_text(data: dict) -> str:
    lines = [
        "🏢 HEARING READINESS",
        f"📅 {data['date'].strftime('%d %b %Y')}",
        "",
        f"Selected physical files: {data['total']}",
        f"Files brought: {data['brought']}",
        f"Ready cases: {data['ready']}",
        f"Attention needed: {data['attention']}",
        f"Not ready: {data['not_ready']}",
        f"Missing files: {data['missing']}",
        "",
        f"Office readiness: {data['score']}%",
    ]
    if data["reasons"]:
        lines.extend(["", "Reasons:"] + [f"• {reason}" for reason in data["reasons"]])
    return "\n".join(lines)


def _case_text(row: dict) -> str:
    icon = "🟢" if row["readiness"] == "READY" else ("🟡" if row["readiness"] == "ATTENTION" else "🔴")
    lines = [
        f"{icon} {row['readiness']} — {row['score']}%",
        f"{row.get('case_number') or 'Case number not entered'}",
        f"{row.get('case_title') or 'Title not recorded'}",
        f"Court: {row.get('court') or '-'} | Floor {row.get('floor') or '-'} | Room {row.get('room') or '-'}",
        f"Purpose: {row.get('purpose') or '-'}",
        f"Owner: {row.get('owner') or 'Not assigned'}",
    ]
    if row["exceptions"]:
        lines.extend(["", "Pending / missing:"] + [f"• {item}" for item in row["exceptions"]])
    else:
        lines.extend(["", "✅ File, ownership, purpose and pending-work checks are clear."])
    return "\n".join(lines)


async def readiness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = _target_date(context.args)
    data = await asyncio.to_thread(readiness_summary, target)
    await update.effective_message.reply_text(_summary_text(data))
    for row in data["rows"]:
        if row["readiness"] != "READY":
            await update.effective_message.reply_text(_case_text(row))


async def morningreadiness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.args = ["today"]
    await readiness(update, context)
