"""Administrator Telegram interface for eCourts backup reconciliation."""
from __future__ import annotations

import asyncio
import html
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from services.ecourts_backup_service import (
    approve_link,
    create_reconciled_drive_export,
    ensure_ecourts_schema,
    latest_reconciliation,
    synchronize_backups,
)

logger = logging.getLogger(__name__)


def _admin(user_id: int | None) -> bool:
    values = [
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("AI_ADMIN_USER_IDS", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    ]
    allowed: set[int] = set()
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                allowed.add(int(item))
    return bool(user_id is not None and int(user_id) in allowed)


async def _authorize(update: Update) -> bool:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "🔒 eCourts reconciliation is available only in Ajay’s private chat."
        )
        return False
    if not _admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ eCourts administration access denied.")
        return False
    return True


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Synchronize Backups", callback_data="ecr:sync")],
        [
            InlineKeyboardButton("🔴 Missing from Backup", callback_data="ecr:office"),
            InlineKeyboardButton("🔵 Backup Only", callback_data="ecr:backup"),
        ],
        [
            InlineKeyboardButton("🟠 Possible Matches", callback_data="ecr:possible"),
            InlineKeyboardButton("⚠️ Conflicts", callback_data="ecr:conflicts"),
        ],
        [InlineKeyboardButton("📤 Create Reconciled Copy", callback_data="ecr:export")],
        [InlineKeyboardButton("❌ Close", callback_data="ecr:close")],
    ])


def _summary(data: dict) -> str:
    if data.get("status") == "NOT_RUN":
        return (
            "⚖️ <b>eCOURTS RECONCILIATION</b>\n\n"
            "No backup synchronization has been run yet.\n"
            "Tap <b>Synchronize Backups</b> to safely read the two Drive files."
        )
    status = "✅ Successful" if data.get("status") == "SUCCESS" else "❌ Failed"
    return (
        "⚖️ <b>eCOURTS RECONCILIATION</b>\n\n"
        f"Last run: <b>{status}</b>\n"
        f"District backup: <b>{data.get('district_count', 0)}</b>\n"
        f"High Court backup: <b>{data.get('high_court_count', 0)}</b>\n\n"
        f"✅ Matched: <b>{data.get('matched_count', 0)}</b>\n"
        f"🟠 Possible matches: <b>{data.get('possible_count', 0)}</b>\n"
        f"🔴 Office cases missing from backup: <b>{data.get('office_only_count', 0)}</b>\n"
        f"🔵 Backup cases missing from Office OS: <b>{data.get('backup_only_count', 0)}</b>\n"
        f"⚠️ Conflicts: <b>{data.get('conflict_count', 0)}</b>\n\n"
        "Original eCourts backup files remain unchanged."
    )


async def ecourts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    await asyncio.to_thread(ensure_ecourts_schema)
    data = await asyncio.to_thread(latest_reconciliation)
    await update.effective_message.reply_text(
        _summary(data), parse_mode=ParseMode.HTML, reply_markup=_keyboard()
    )


async def syncecourts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    waiting = await update.effective_message.reply_text(
        "⏳ Reading the eCourts backups from Google Drive and reconciling Office OS cases…"
    )
    try:
        data = await asyncio.to_thread(
            synchronize_backups,
            update.effective_user.id if update.effective_user else None,
        )
        await waiting.edit_text(
            _summary(data), parse_mode=ParseMode.HTML, reply_markup=_keyboard()
        )
    except Exception as exc:
        logger.exception("eCourts backup synchronization failed")
        await waiting.edit_text(
            "❌ eCourts synchronization failed safely.\n"
            "No Office OS case or original backup was changed.\n\n"
            f"Reason: {type(exc).__name__}: {str(exc)[:800]}"
        )


def _render_list(kind: str, data: dict) -> str:
    headings = {
        "office": "🔴 OFFICE CASES MISSING FROM eCOURTS BACKUP",
        "backup": "🔵 eCOURTS BACKUP CASES MISSING FROM OFFICE OS",
        "possible": "🟠 POSSIBLE MATCHES — ADMIN APPROVAL REQUIRED",
        "conflicts": "⚠️ CONFLICTING RECORDS",
    }
    lines = [f"<b>{headings[kind]}</b>", ""]
    items = {
        "office": data.get("office_only", []),
        "backup": data.get("backup_only", []),
        "possible": data.get("possible", []),
        "conflicts": data.get("conflicts", []),
    }[kind]
    if not items:
        lines.append("No records in this category.")
    for index, item in enumerate(items[:30], start=1):
        if kind == "office":
            lines.append(
                f"{index}. <b>{html.escape(str(item.get('_number') or '-'))}</b>\n"
                f"   {html.escape(str(item.get('case_title') or item.get('client_name') or '-'))}\n"
                f"   Local ID: <code>{html.escape(str(item.get('_pk')))}</code> · CNR required"
            )
        elif kind == "backup":
            lines.append(
                f"{index}. <b>{html.escape(str(item.get('display_case_number') or '-'))}</b>\n"
                f"   {html.escape(str(item.get('petitioner_name') or '-'))} vs "
                f"{html.escape(str(item.get('respondent_name') or '-'))}\n"
                f"   CNR: <code>{html.escape(str(item.get('cino')))}</code>"
            )
        elif kind == "possible":
            local, backup = item["local"], item["backup"]
            lines.append(
                f"{index}. <b>{html.escape(str(local.get('_number') or '-'))}</b> ↔ "
                f"<b>{html.escape(str(backup.get('display_case_number') or '-'))}</b>\n"
                f"   Confidence: {float(item.get('confidence') or 0):.0%}\n"
                f"   Approve: <code>/ecourtsapprove {html.escape(str(local.get('_pk')))} "
                f"{html.escape(str(backup.get('cino')))}</code>"
            )
        else:
            local = item.get("local") or {}
            lines.append(
                f"{index}. <b>{html.escape(str(local.get('_number') or '-'))}</b>\n"
                f"   {html.escape(str(item.get('reason') or 'Review required'))}"
            )
        lines.append("")
    if len(items) > 30:
        lines.append(f"Showing 30 of {len(items)} records.")
    return "\n".join(lines)[:4000]


async def ecourtsmissing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    data = await asyncio.to_thread(latest_reconciliation)
    await update.effective_message.reply_text(
        _render_list("office", data), parse_mode=ParseMode.HTML
    )


async def ecourtsapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage: /ecourtsapprove LOCAL_CASE_ID CNR\n"
            "Use the exact suggestion shown under Possible Matches."
        )
        return
    try:
        await asyncio.to_thread(
            approve_link, context.args[0], context.args[1], update.effective_user.id
        )
        await update.effective_message.reply_text(
            "✅ eCourts link approved and recorded in the audit log."
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Approval failed: {exc}")


async def ecourts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _authorize(update):
        return
    action = (query.data or "").split(":")[-1]
    if action == "close":
        await query.edit_message_text("eCourts reconciliation closed.")
        return
    if action == "sync":
        await query.edit_message_text("⏳ Synchronizing both Drive backups…")
        try:
            data = await asyncio.to_thread(synchronize_backups, update.effective_user.id)
            await query.edit_message_text(
                _summary(data), parse_mode=ParseMode.HTML, reply_markup=_keyboard()
            )
        except Exception as exc:
            logger.exception("eCourts callback sync failed")
            await query.edit_message_text(
                f"❌ Synchronization failed safely: {type(exc).__name__}: {str(exc)[:800]}"
            )
        return
    if action == "export":
        await query.edit_message_text("⏳ Creating a new reconciled copy in Google Drive…")
        try:
            created = await asyncio.to_thread(
                create_reconciled_drive_export, update.effective_user.id
            )
            file_lines = []
            for item in created:
                link = item.get("webViewLink") or f"https://drive.google.com/file/d/{item['id']}/view"
                file_lines.append(f"{item.get('name')}\n{link}")
            await query.edit_message_text(
                "✅ Reconciled copies created.\n\n"
                + "\n\n".join(file_lines)
                + "\n\n"
                "The original eCourts backups were not changed.",
                disable_web_page_preview=True,
                reply_markup=_keyboard(),
            )
        except Exception as exc:
            await query.edit_message_text(f"❌ Export failed safely: {exc}", reply_markup=_keyboard())
        return
    data = await asyncio.to_thread(latest_reconciliation)
    await query.message.reply_text(
        _render_list(action, data), parse_mode=ParseMode.HTML
    )


async def ecourts_backup_sync_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(synchronize_backups, None)
    except Exception:
        logger.exception("Scheduled eCourts backup synchronization failed")


def register_ecourts_handlers(app) -> None:
    app.add_handler(CommandHandler("ecourts", ecourts))
    app.add_handler(CommandHandler("syncecourts", syncecourts))
    app.add_handler(CommandHandler("ecourtsmissing", ecourtsmissing))
    app.add_handler(CommandHandler("ecourtsapprove", ecourtsapprove))
    app.add_handler(CallbackQueryHandler(ecourts_callback, pattern=r"^ecr:"))
