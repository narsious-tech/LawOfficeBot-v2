"""Sprint 15.0.3 commands and scheduled 5:00 PM dispatch."""
from __future__ import annotations

import os
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.case_intelligence_service import (
    advocate_diaries_pending_cases,
    jimmy_telegram_id,
    pending_grouped_by_owner,
    render_office_report,
    render_pending_cases,
    render_updated_cases,
    split_html_message,
    staff_telegram_id,
    todays_next_dates,
)


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    for chunk in split_html_message(text):
        await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML)


async def nextdateslist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    updated = todays_next_dates()
    try:
        pending = advocate_diaries_pending_cases()
    except Exception as exc:
        pending = []
        print(f"Sprint 15.0.3 pending-case mirror failed: {exc}")
    await update.effective_message.reply_text(
        render_office_report(updated, pending), parse_mode=ParseMode.HTML
    )


async def physical_file_next_dates_job(context: ContextTypes.DEFAULT_TYPE):
    updated = todays_next_dates()
    try:
        pending = advocate_diaries_pending_cases()
    except Exception as exc:
        pending = []
        print(f"Sprint 15.0.3 pending-case mirror failed: {exc}")

    # Jimmy: compact physical-file changes only.
    jimmy = jimmy_telegram_id()
    if jimmy:
        try:
            await _send(context, jimmy, render_updated_cases(updated))
        except Exception as exc:
            print(f"Sprint 15.0.3 Jimmy dispatch failed: {exc}")

    # Office group receives the complete report at the same time.
    group = os.getenv("PHYSICAL_FILE_GROUP_CHAT_ID") or os.getenv("OFFICE_GROUP_CHAT_ID")
    admin = os.getenv("ADMIN_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    destinations = []
    for raw in (group, admin):
        if raw:
            try:
                chat_id = int(raw)
                if chat_id not in destinations:
                    destinations.append(chat_id)
            except ValueError:
                print(f"Sprint 15.0.3 invalid chat id: {raw}")
    office_report = render_office_report(updated, pending)
    for chat_id in destinations:
        try:
            await _send(context, chat_id, office_report)
        except Exception as exc:
            print(f"Sprint 15.0.3 office dispatch failed for {chat_id}: {exc}")

    # Each case owner receives only their own pending matters.
    grouped = pending_grouped_by_owner(pending)
    for owner, rows in grouped.items():
        owner_id = staff_telegram_id(owner)
        if not owner_id:
            print(f"Sprint 15.0.3 no Telegram account linked for case owner {owner}")
            continue
        text = render_pending_cases(rows, heading=f"{owner.upper()} — YOUR PENDING CASES")
        text += "\n\nPlease complete the missing Advocate Diaries updates."
        try:
            await _send(context, owner_id, text)
        except Exception as exc:
            print(f"Sprint 15.0.3 owner dispatch failed for {owner}: {exc}")

    # Priya receives all pending matters grouped by owner for supervision.
    priya = staff_telegram_id("Priya")
    if priya and pending:
        sections = ["👩‍💼 <b>WORK SUPERVISION — PENDING CASE UPDATES</b>", ""]
        for owner, rows in sorted(grouped.items()):
            sections.append(render_pending_cases(rows, heading=owner.upper()))
            sections.append("")
        sections.append(f"<b>Total Pending: {len(pending)}</b>")
        try:
            await _send(context, priya, "\n".join(sections))
        except Exception as exc:
            print(f"Sprint 15.0.3 Priya dispatch failed: {exc}")
