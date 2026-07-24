"""Administrator Telegram interface for eCourts backup reconciliation."""
from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from services.ecourts_backup_service import (
    approve_link,
    create_reconciled_drive_export,
    ecourts_operations_summary,
    ensure_ecourts_schema,
    inspect_backup_record,
    list_ecourts_changes,
    latest_reconciliation,
    mark_ecourts_changes_alerted,
    reject_match,
    review_ecourts_change,
    synchronize_backups,
)
from services.ecourts_order_service import (
    list_orders,
    mark_orders_alerted,
    scan_order_inbox,
)

logger = logging.getLogger(__name__)


def _admin_destinations() -> list[int]:
    values = [
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("AI_ADMIN_USER_IDS", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    ]
    result: set[int] = set()
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                result.add(int(item))
    return sorted(result)


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
        [InlineKeyboardButton("🛡 Pending Change Review", callback_data="ecr:review")],
        [
            InlineKeyboardButton("🔴 Not Linked", callback_data="ecr:office:1"),
            InlineKeyboardButton("🔵 Backup Only", callback_data="ecr:backup"),
        ],
        [
            InlineKeyboardButton("🟠 Possible Matches", callback_data="ecr:possible"),
            InlineKeyboardButton("⚠️ Conflicts", callback_data="ecr:conflicts"),
        ],
        [InlineKeyboardButton("📊 Download Full Report", callback_data="ecr:report")],
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
        f"🔴 No backup candidate found: <b>{data.get('no_candidate_count', 0)}</b>\n"
        f"🔵 Backup cases missing from Office OS: <b>{data.get('backup_only_count', 0)}</b>\n"
        f"   • Active: <b>{data.get('backup_only_active_count', 0)}</b>\n"
        f"   • Disposed: <b>{data.get('backup_only_disposed_count', 0)}</b>\n"
        f"   • Unknown: <b>{data.get('backup_only_unknown_count', 0)}</b>\n"
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
        if data.get("change_count"):
            await update.effective_message.reply_text(
                f"🔔 {int(data['change_count'])} eCourts field change(s) detected.\n"
                "Use /ecourtschanges to review them."
            )
    except Exception as exc:
        logger.exception("eCourts backup synchronization failed")
        await waiting.edit_text(
            "❌ eCourts synchronization failed safely.\n"
            "No Office OS case or original backup was changed.\n\n"
            f"Reason: {type(exc).__name__}: {str(exc)[:800]}"
        )


def _render_list(kind: str, data: dict, page: int = 1, page_size: int = 15) -> str:
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
    page = max(1, int(page or 1))
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    lines.append(f"Page {page}/{total_pages} · Total {len(items)}")
    lines.append("")
    if not items:
        lines.append("No records in this category.")
    possible_ids = {
        str(item["local"]["_pk"])
        for item in data.get("possible", [])
    }
    for index, item in enumerate(page_items, start=start + 1):
        if kind == "office":
            state = (
                "🟠 Possible match available"
                if str(item.get("_pk")) in possible_ids
                else "🔴 No candidate · CNR required"
            )
            lines.append(
                f"{index}. <b>{html.escape(str(item.get('_number') or '-'))}</b>\n"
                f"   {html.escape(str(item.get('case_title') or item.get('client_name') or '-'))}\n"
                f"   Local ID: <code>{html.escape(str(item.get('_pk')))}</code>\n"
                f"   {state}"
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
                f"   Safety: {html.escape(str(item.get('match_strength') or 'VERIFY'))}\n"
                f"   Conservative score: {float(item.get('confidence') or 0):.0%}\n"
                f"   Parties: {float(item.get('party_score') or 0):.0%} similar\n"
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
    return "\n".join(lines)[:4000]


def _page_keyboard(kind: str, page: int, total: int, page_size: int = 15):
    pages = max(1, (total + page_size - 1) // page_size)
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"ecr:{kind}:{page-1}"))
    if page < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ecr:{kind}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("📊 Download Full Report", callback_data="ecr:report")])
    rows.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="ecr:home")])
    return InlineKeyboardMarkup(rows)


async def ecourtsmissing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    data = await asyncio.to_thread(latest_reconciliation)
    page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
    await update.effective_message.reply_text(
        _render_list("office", data, page),
        parse_mode=ParseMode.HTML,
        reply_markup=_page_keyboard(
            "office", page, len(data.get("office_only", []))
        ),
    )


def _report_bytes(data: dict) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "Category", "Local ID", "Office Case Number", "Office Title",
        "CNR", "eCourts Case Number", "Petitioner", "Respondent",
        "eCourts Status", "Confidence", "Required Action",
    ])
    possible_by_local = {
        str(item["local"]["_pk"]): item
        for item in data.get("possible", [])
    }
    for local in data.get("office_only", []):
        possible = possible_by_local.get(str(local.get("_pk")))
        backup = possible.get("backup") if possible else {}
        writer.writerow([
            "POSSIBLE_MATCH" if possible else "NO_BACKUP_CANDIDATE",
            local.get("_pk"), local.get("_number"),
            local.get("case_title") or local.get("client_name"),
            backup.get("cino"), backup.get("display_case_number"),
            backup.get("petitioner_name"), backup.get("respondent_name"), "",
            f"{float(possible.get('confidence')):.2%}" if possible else "",
            "Approve after verification" if possible else "Locate/add CNR",
        ])
    for item in data.get("backup_only", []):
        if item in data.get("backup_only_disposed", []):
            state = "DISPOSED"
        elif item in data.get("backup_only_active", []):
            state = "ACTIVE"
        else:
            state = "UNKNOWN"
        writer.writerow([
            f"BACKUP_ONLY_{state}", "", "", "", item.get("cino"),
            item.get("display_case_number"), item.get("petitioner_name"),
            item.get("respondent_name"), state, "", "Review before importing",
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


async def ecourtsreport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    data = await asyncio.to_thread(latest_reconciliation)
    content = _report_bytes(data)
    await update.effective_message.reply_document(
        document=InputFile(io.BytesIO(content), filename="ecourts-reconciliation.csv"),
        caption=(
            "📊 Full eCourts reconciliation report\n"
            "Includes every unlinked Office case, possible match, and backup-only case."
        ),
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


def _field_lines(fields: list[str], empty_text: str) -> str:
    if not fields:
        return empty_text
    return "\n".join(f"• <code>{html.escape(name)}</code>" for name in fields)


async def ecourtsinspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect the field names stored in one eCourts app backup record."""
    if not await _authorize(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: <code>/ecourtsinspect 16_CHARACTER_CNR</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        result = await asyncio.to_thread(inspect_backup_record, context.args[0])
        text = (
            "🔍 <b>eCOURTS BACKUP INSPECTION</b>\n\n"
            f"CNR: <code>{html.escape(result['cino'])}</code>\n"
            f"Case: <b>{html.escape(str(result.get('display_case_number') or '-'))}</b>\n"
            f"Backup: <b>{html.escape(str(result.get('source_kind') or '-'))}</b>\n"
            f"Fields stored: <b>{int(result.get('field_count') or 0)}</b>\n\n"
            "📄 <b>ORDER / DOCUMENT FIELDS</b>\n"
            f"{_field_lines(result['order_fields'], '❌ None found')}\n\n"
            "🔗 <b>REFERENCE / DOWNLOAD-LIKE FIELDS</b>\n"
            f"{_field_lines(result['reference_fields'], '❌ None found')}\n\n"
            "🧾 <b>ALL POPULATED FIELD NAMES</b>\n"
            f"{_field_lines(result['populated_fields'], 'No populated fields found.')}\n\n"
            "Only field names are shown; backup values are not exposed."
        )
        await update.effective_message.reply_text(
            text[:4096], parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Inspection failed safely: {html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
        )


FIELD_LABELS = {
    "next_hearing_date": "📅 Next hearing date",
    "last_hearing_date": "⏮ Last hearing date",
    "purpose_name": "📝 Hearing purpose",
    "court_designation": "🏛 Court / Judge",
    "decision_date": "⚖️ Decision date",
    "disposal_name": "🏁 Disposal status",
    "updated": "🔄 eCourts record updated",
}


def _change_text(item: dict) -> str:
    icon = "🚨" if item.get("severity") == "CRITICAL" else (
        "⚠️" if item.get("severity") == "IMPORTANT" else "ℹ️"
    )
    label = FIELD_LABELS.get(item.get("field_name"), item.get("field_name") or "Field")
    return (
        f"{icon} <b>{html.escape(str(item.get('display_case_number') or '-'))}</b>\n"
        f"CNR: <code>{html.escape(str(item.get('cino') or '-'))}</code>\n"
        f"{label}\n"
        f"Previous: <code>{html.escape(str(item.get('old_value') or 'Not recorded'))}</code>\n"
        f"New: <code>{html.escape(str(item.get('new_value') or 'Not recorded'))}</code>"
    )


async def ecourtschanges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
    rows = await asyncio.to_thread(list_ecourts_changes, 200, False)
    page_size = 8
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    selected = rows[(page - 1) * page_size:page * page_size]
    lines = [
        "🔔 <b>eCOURTS CHANGE HISTORY</b>",
        f"Page {page}/{pages} · Latest {len(rows)} change(s)",
        "",
    ]
    if not selected:
        lines.append("No changes have been detected yet.")
    else:
        lines.extend(_change_text(item) + "\n" for item in selected)
    await update.effective_message.reply_text(
        "\n".join(lines)[:4096], parse_mode=ParseMode.HTML,
    )


def _review_keyboard(item: dict) -> InlineKeyboardMarkup:
    change_id = int(item["id"])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve & Apply Safely",
                callback_data=f"ecr:changeapprove:{change_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"ecr:changereject:{change_id}",
            ),
        ],
        [InlineKeyboardButton("⬅️ eCourts Dashboard", callback_data="ecr:home")],
    ])


def _operations_text(data: dict) -> str:
    last_sync = data.get("last_sync_at")
    last_sync_text = (
        last_sync.strftime("%d-%m-%Y %I:%M %p")
        if hasattr(last_sync, "strftime") else "Not yet"
    )
    return (
        "⚖️ <b>eCOURTS OPERATIONS DESK</b>\n\n"
        f"🔄 Last backup sync: <b>{html.escape(last_sync_text)}</b>\n"
        f"Status: <b>{html.escape(str(data.get('last_sync_status') or '-'))}</b>\n\n"
        "🛡 <b>ADMIN REVIEW QUEUE</b>\n"
        f"🚨 Critical changes: <b>{int(data.get('critical_changes') or 0)}</b>\n"
        f"⚠️ Important changes: <b>{int(data.get('important_changes') or 0)}</b>\n"
        f"📋 Total pending: <b>{int(data.get('pending_changes') or 0)}</b>\n\n"
        "🔗 <b>CASE LINK HEALTH</b>\n"
        f"✅ Linked cases: <b>{int(data.get('linked_cases') or 0)}</b>\n"
        f"🔴 Active cases without CNR: <b>{int(data.get('missing_cnr') or 0)}</b>\n\n"
        "📄 <b>ORDER INBOX</b>\n"
        f"⚠️ Important/critical orders: <b>{int(data.get('important_orders') or 0)}</b>\n"
        f"🟠 Unmatched PDFs: <b>{int(data.get('unmatched_orders') or 0)}</b>\n"
        f"❌ Failed PDFs: <b>{int(data.get('failed_orders') or 0)}</b>\n\n"
        "Office OS values are changed only after your approval."
    )


async def _send_pending_change(message) -> None:
    rows = await asyncio.to_thread(list_ecourts_changes, 1, False, "PENDING")
    if not rows:
        await message.reply_text(
            "✅ No eCourts changes are waiting for administrator review."
        )
        return
    item = rows[0]
    await message.reply_text(
        "🛡 <b>eCOURTS CHANGE — ADMIN DECISION</b>\n\n"
        + _change_text(item)
        + "\n\nApprove only after checking the CNR and case. Unsupported fields "
        "are recorded as approved but are never forced into Office OS.",
        parse_mode=ParseMode.HTML,
        reply_markup=_review_keyboard(item),
    )


async def ecourtsreview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    await _send_pending_change(update.effective_message)


async def _send_possible_match(message) -> None:
    data = await asyncio.to_thread(latest_reconciliation)
    rows = data.get("possible", [])
    if not rows:
        await message.reply_text(
            "✅ No safe possible matches are waiting for verification.\n"
            "Unsafe duplicate-CNR suggestions are shown under Conflicts."
        )
        return
    item = rows[0]
    local, backup = item["local"], item["backup"]
    reasons = "\n".join(
        f"• {html.escape(str(reason))}" for reason in item.get("reasons", [])
    )
    local_title = local.get("case_title") or (
        f"{local.get('client_name') or '-'} vs {local.get('opposite_party') or '-'}"
    )
    backup_title = (
        f"{backup.get('petitioner_name') or '-'} vs "
        f"{backup.get('respondent_name') or '-'}"
    )
    await message.reply_text(
        "🟠 <b>POSSIBLE eCOURTS MATCH</b>\n\n"
        "<b>OFFICE OS</b>\n"
        f"Case: <b>{html.escape(str(local.get('_number') or '-'))}</b>\n"
        f"Title: {html.escape(str(local_title))}\n"
        f"Court: {html.escape(str(local.get('court_name') or '-'))}\n\n"
        "<b>eCOURTS BACKUP</b>\n"
        f"Case: <b>{html.escape(str(backup.get('display_case_number') or '-'))}</b>\n"
        f"Title: {html.escape(backup_title)}\n"
        f"Court: {html.escape(str(backup.get('court_designation') or '-'))}\n"
        f"CNR: <code>{html.escape(str(backup.get('cino') or '-'))}</code>\n\n"
        f"Safety: <b>{html.escape(str(item.get('match_strength') or 'VERIFY'))}</b>\n"
        f"Conservative score: <b>{float(item.get('confidence') or 0):.0%}</b>\n"
        f"{reasons}\n\n"
        "Approve only after confirming that both records are the same case.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Same Case — Approve",
                callback_data=(
                    f"ecr:matchapprove:{local['_pk']}:{backup['cino']}"
                ),
            )],
            [InlineKeyboardButton(
                "❌ Not the Same Case",
                callback_data=(
                    f"ecr:matchreject:{local['_pk']}:{backup['cino']}"
                ),
            )],
            [InlineKeyboardButton("⬅️ eCourts Dashboard", callback_data="ecr:home")],
        ]),
    )


async def ecourtsmatches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    await _send_possible_match(update.effective_message)


async def ecourtsops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    data = await asyncio.to_thread(ecourts_operations_summary)
    await update.effective_message.reply_text(
        _operations_text(data),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛡 Review Pending Changes", callback_data="ecr:review")],
            [
                InlineKeyboardButton("🔄 Sync Backups", callback_data="ecr:sync"),
                InlineKeyboardButton("📥 Scan Orders", callback_data="ecr:orderscan"),
            ],
        ]),
    )


def _order_text(item: dict, include_summary: bool = False) -> str:
    processing_status = item.get("processing_status") or item.get("status")
    status_icons = {
        "ARCHIVED": "✅", "MATCHED": "🟢", "DUPLICATE": "♻️",
        "UNMATCHED": "🟠", "FAILED": "❌",
    }
    importance_icons = {"CRITICAL": "🚨", "IMPORTANT": "⚠️", "NORMAL": "📄"}
    lines = [
        f"{importance_icons.get(item.get('importance'), '📄')} "
        f"<b>{html.escape(str(item.get('original_name') or 'Order PDF'))}</b>",
        f"{status_icons.get(processing_status, 'ℹ️')} "
        f"Status: <b>{html.escape(str(processing_status or '-'))}</b>",
        f"Case: <b>{html.escape(str(item.get('case_number') or 'Not matched'))}</b>",
        f"CNR: <code>{html.escape(str(item.get('cino') or '-'))}</code>",
    ]
    link = item.get("archived_drive_link") or item.get("original_link")
    if link:
        lines.append(f"🔗 {html.escape(str(link))}")
    if item.get("error_message"):
        lines.append(f"Reason: {html.escape(str(item['error_message'])[:500])}")
    if include_summary and item.get("ai_summary"):
        lines.extend(["", "🤖 <b>AI WORKING NOTE</b>", html.escape(str(item["ai_summary"]))])
    return "\n".join(lines)


async def ecourtsorders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    rows = await asyncio.to_thread(list_orders, 10, False)
    if not rows:
        await update.effective_message.reply_text(
            "📥 No order PDFs have been processed yet.\n\n"
            "Place PDFs in the Drive folder <b>eCourts Order Inbox</b>, then run "
            "<code>/syncecourtsorders</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    for item in rows:
        await update.effective_message.reply_text(
            _order_text(item, include_summary=False)[:4096],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def syncecourtsorders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    waiting = await update.effective_message.reply_text(
        "⏳ Scanning the Google Drive eCourts Order Inbox…"
    )
    try:
        result = await asyncio.to_thread(scan_order_inbox, 10, True)
        await waiting.edit_text(
            "✅ Order Inbox scan complete.\n\n"
            f"PDFs present: {result['files_seen']}\n"
            f"Processed/retried: {result['processed_count']}\n\n"
            "Use /ecourtsorders to review the results."
        )
        for item in result["results"]:
            await update.effective_message.reply_text(
                _order_text(item, include_summary=True)[:4096],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logger.exception("eCourts order inbox scan failed")
        await waiting.edit_text(
            f"❌ Order Inbox scan failed safely: {type(exc).__name__}: {str(exc)[:800]}"
        )


async def _alert_changes(context: ContextTypes.DEFAULT_TYPE) -> None:
    changes = await asyncio.to_thread(list_ecourts_changes, 100, True)
    if not changes:
        return
    destinations = _admin_destinations()
    sent = False
    for destination in destinations:
        for item in changes:
            try:
                await context.bot.send_message(
                    chat_id=destination,
                    text="🔔 <b>eCOURTS CHANGE DETECTED</b>\n\n" + _change_text(item),
                    parse_mode=ParseMode.HTML,
                )
                sent = True
            except Exception:
                logger.exception("Could not deliver eCourts change alert")
    if sent:
        await asyncio.to_thread(
            mark_ecourts_changes_alerted, [int(item["id"]) for item in changes]
        )


async def _alert_orders(context: ContextTypes.DEFAULT_TYPE) -> None:
    orders = await asyncio.to_thread(list_orders, 25, True)
    if not orders:
        return
    destinations = _admin_destinations()
    alerted: list[int] = []
    for item in orders:
        delivered = False
        for destination in destinations:
            try:
                await context.bot.send_message(
                    chat_id=destination,
                    text="📥 <b>NEW eCOURTS ORDER PDF</b>\n\n"
                    + _order_text(item, include_summary=True)[:3900],
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                delivered = True
            except Exception:
                logger.exception("Could not deliver eCourts order alert")
        if delivered:
            alerted.append(int(item["id"]))
    if alerted:
        await asyncio.to_thread(mark_orders_alerted, alerted)


async def ecourts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _authorize(update):
        return
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    if action == "close":
        await query.edit_message_text("eCourts reconciliation closed.")
        return
    if action == "home":
        data = await asyncio.to_thread(latest_reconciliation)
        await query.edit_message_text(
            _summary(data), parse_mode=ParseMode.HTML, reply_markup=_keyboard()
        )
        return
    if action in {"matchapprove", "matchreject"}:
        if len(parts) < 4:
            await query.message.reply_text("❌ Invalid match reference.")
            return
        local_pk, cino = parts[2], parts[3]
        try:
            if action == "matchapprove":
                await asyncio.to_thread(
                    approve_link, local_pk, cino, update.effective_user.id
                )
                result_text = (
                    "✅ Match approved. The CNR is now linked to the Office OS case."
                )
            else:
                await asyncio.to_thread(
                    reject_match,
                    local_pk,
                    cino,
                    update.effective_user.id,
                    "Rejected from Telegram match review",
                )
                result_text = (
                    "❌ Suggestion rejected. This case/CNR pair will not be "
                    "suggested again."
                )
            await query.edit_message_text(result_text)
            await _send_possible_match(query.message)
        except Exception as exc:
            await query.message.reply_text(
                f"❌ Match decision failed safely: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
        return
    if action == "review":
        await _send_pending_change(query.message)
        return
    if action in {"changeapprove", "changereject"}:
        if len(parts) < 3 or not parts[2].isdigit():
            await query.message.reply_text("❌ Invalid eCourts change reference.")
            return
        try:
            result = await asyncio.to_thread(
                review_ecourts_change,
                int(parts[2]),
                "APPROVE" if action == "changeapprove" else "REJECT",
                update.effective_user.id,
            )
            await query.edit_message_text(
                (
                    "✅ <b>eCOURTS CHANGE REVIEWED</b>\n\n"
                    f"Case: <b>{html.escape(str(result.get('display_case_number') or '-'))}</b>\n"
                    f"Decision: <b>{html.escape(str(result.get('review_status') or '-'))}</b>\n"
                    f"{html.escape(str(result.get('apply_message') or 'Recorded.'))}"
                ),
                parse_mode=ParseMode.HTML,
            )
            await _send_pending_change(query.message)
        except Exception as exc:
            await query.message.reply_text(
                f"❌ Review could not be completed safely: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
        return
    if action == "orderscan":
        await query.message.reply_text("⏳ Scanning the eCourts Order Inbox…")
        try:
            result = await asyncio.to_thread(scan_order_inbox, 10, True)
            await query.message.reply_text(
                "✅ Order Inbox scan complete.\n"
                f"PDFs present: {result['files_seen']}\n"
                f"Processed/retried: {result['processed_count']}"
            )
        except Exception as exc:
            await query.message.reply_text(
                f"❌ Order scan failed safely: {type(exc).__name__}: {str(exc)[:700]}"
            )
        return
    if action == "report":
        data = await asyncio.to_thread(latest_reconciliation)
        content = _report_bytes(data)
        await query.message.reply_document(
            document=InputFile(io.BytesIO(content), filename="ecourts-reconciliation.csv"),
            caption="📊 Complete administrator reconciliation report.",
        )
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
    if action == "possible":
        await _send_possible_match(query.message)
        return
    data = await asyncio.to_thread(latest_reconciliation)
    if action == "backup":
        text = (
            "🔵 <b>BACKUP-ONLY CLASSIFICATION</b>\n\n"
            f"🟢 Active: <b>{data.get('backup_only_active_count', 0)}</b>\n"
            f"⚫ Disposed: <b>{data.get('backup_only_disposed_count', 0)}</b>\n"
            f"⚪ Unknown: <b>{data.get('backup_only_unknown_count', 0)}</b>\n\n"
            "Download the full report for every case and CNR."
        )
        await query.message.reply_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Download Full Report", callback_data="ecr:report")],
                [InlineKeyboardButton("⬅️ Dashboard", callback_data="ecr:home")],
            ]),
        )
        return
    items = {
        "office": data.get("office_only", []),
        "possible": data.get("possible", []),
        "conflicts": data.get("conflicts", []),
    }.get(action, [])
    await query.message.reply_text(
        _render_list(action, data, page),
        parse_mode=ParseMode.HTML,
        reply_markup=_page_keyboard(action, page, len(items)),
    )


async def ecourts_backup_sync_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(synchronize_backups, None)
        await _alert_changes(context)
    except Exception:
        logger.exception("Scheduled eCourts backup synchronization failed")


async def ecourts_order_inbox_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(
            scan_order_inbox,
            max(1, int(os.getenv("ECOURTS_ORDER_MAX_FILES_PER_SCAN", "5"))),
            False,
        )
        await _alert_orders(context)
    except Exception:
        logger.exception("Scheduled eCourts order inbox scan failed")


async def ecourts_daily_operations_job(context: ContextTypes.DEFAULT_TYPE):
    """Send a private read-only daily summary to configured administrators."""
    try:
        data = await asyncio.to_thread(ecourts_operations_summary)
        for destination in _admin_destinations():
            try:
                await context.bot.send_message(
                    chat_id=destination,
                    text=_operations_text(data),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "🛡 Review Pending Changes",
                            callback_data="ecr:review",
                        )],
                        [InlineKeyboardButton(
                            "⚖️ Open eCourts Desk",
                            callback_data="ecr:home",
                        )],
                    ]),
                )
            except Exception:
                logger.exception("Could not deliver daily eCourts operations summary")
    except Exception:
        logger.exception("Daily eCourts operations summary failed")


def register_ecourts_handlers(app) -> None:
    app.add_handler(CommandHandler("ecourts", ecourts))
    app.add_handler(CommandHandler("syncecourts", syncecourts))
    app.add_handler(CommandHandler("ecourtsmissing", ecourtsmissing))
    app.add_handler(CommandHandler("ecourtsreport", ecourtsreport))
    app.add_handler(CommandHandler("ecourtsapprove", ecourtsapprove))
    app.add_handler(CommandHandler("ecourtsinspect", ecourtsinspect))
    app.add_handler(CommandHandler("ecourtschanges", ecourtschanges))
    app.add_handler(CommandHandler("ecourtsreview", ecourtsreview))
    app.add_handler(CommandHandler("ecourtsmatches", ecourtsmatches))
    app.add_handler(CommandHandler("ecourtsops", ecourtsops))
    app.add_handler(CommandHandler("ecourtsorders", ecourtsorders))
    app.add_handler(CommandHandler("syncecourtsorders", syncecourtsorders))
    app.add_handler(CallbackQueryHandler(ecourts_callback, pattern=r"^ecr:"))
