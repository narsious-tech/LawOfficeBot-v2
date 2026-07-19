"""Menu-driven Case Workspace for LawOfficeBot v3 Sprint 3."""

from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.case_timeline_service import get_case_timeline, render_timeline
from services.case_document_service import document_counts, list_case_documents, render_document_list
from services.case_workspace_service import (
    CaseSummary,
    get_case,
    get_case_counts,
    get_case_staff,
    get_case_tasks,
    get_fee_installments,
    recent_cases,
    search_cases,
)


def esc(value) -> str:
    return html.escape(str(value or "-"))


def case_label(case: CaseSummary) -> str:
    primary = case.case_number if case.case_number != "-" else case.case_id
    title = case.case_title if case.case_title != "-" else case.client_name
    text = f"{primary} · {title}"
    return text[:60]


def cases_keyboard(cases: list[CaseSummary]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(case_label(case), callback_data=f"casews:open:{case.db_id}")]
        for case in cases
    ]
    rows.append([InlineKeyboardButton("🔎 Search", callback_data="casews:search_help")])
    rows.append([InlineKeyboardButton("🏠 Dashboard", callback_data="casews:dashboard")])
    return InlineKeyboardMarkup(rows)


def workspace_keyboard(case_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Hearing", callback_data=f"casews:hearing:{case_id}"),
            InlineKeyboardButton("✅ Tasks", callback_data=f"casews:tasks:{case_id}"),
        ],
        [
            InlineKeyboardButton("📂 Documents", callback_data=f"casews:documents:{case_id}"),
            InlineKeyboardButton("💰 Fees", callback_data=f"casews:fees:{case_id}"),
        ],
        [
            InlineKeyboardButton("📝 Notes", callback_data=f"casews:notes:{case_id}"),
            InlineKeyboardButton("👥 Staff", callback_data=f"casews:staff:{case_id}"),
        ],
        [
            InlineKeyboardButton("📋 Works", callback_data=f"casews:works:{case_id}"),
            InlineKeyboardButton("📜 Timeline", callback_data=f"casews:timeline:{case_id}"),
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"casews:open:{case_id}")],
        [InlineKeyboardButton("⬅️ Cases", callback_data="casews:list")],
    ])


def back_keyboard(case_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Case Workspace", callback_data=f"casews:open:{case_id}")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="casews:dashboard")],
    ])


def render_workspace(case: CaseSummary) -> str:
    counts = get_case_counts(case)
    identifier = case.case_number if case.case_number != "-" else case.case_id
    return (
        "⚖️ <b>CASE WORKSPACE</b>\n\n"
        f"🆔 <b>{esc(identifier)}</b>\n"
        f"📌 {esc(case.case_title)}\n"
        f"👤 {esc(case.client_name)}\n"
        f"📱 {esc(case.mobile)}\n\n"
        f"🏛 {esc(case.court_name)}\n"
        f"👨‍⚖️ {esc(case.judge_name)}\n"
        f"👥 Opposite: {esc(case.opposite_party)}\n"
        f"📅 Next Hearing: <b>{esc(case.next_hearing)}</b>\n"
        f"📍 Status: <b>{esc(case.status)}</b>\n\n"
        f"✅ Pending Tasks: <b>{counts['pending_tasks']}</b>\n"
        f"☑️ Completed Tasks: <b>{counts['completed_tasks']}</b>\n"
        f"💳 Fee Entries: <b>{counts['installments']}</b>\n"
        f"👥 Assigned Staff: <b>{counts['staff']}</b>\n\n"
        f"🔗 AD Sync: {esc(case.ad_sync_status)}"
    )


async def case_workspace_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cases = recent_cases(10)
    text = (
        "⚖️ <b>CASE WORKSPACE</b>\n\n"
        "Open a recent case below, or search using:\n"
        "<code>/casesearch name, case number or mobile</code>"
    )
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=cases_keyboard(cases),
    )


async def casesearch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text(
            "🔎 <b>SEARCH CASES</b>\n\n"
            "Use: <code>/casesearch search words</code>\n\n"
            "Examples:\n"
            "<code>/casesearch Rakesh Garg</code>\n"
            "<code>/casesearch CA/603/2019</code>\n"
            "<code>/casesearch 98765</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    cases = search_cases(query, 15)
    if not cases:
        await update.effective_message.reply_text(
            f"❌ No case found for: <b>{esc(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.effective_message.reply_text(
        f"🔎 <b>CASE SEARCH</b>\nFound {len(cases)} result(s) for: {esc(query)}",
        parse_mode=ParseMode.HTML,
        reply_markup=cases_keyboard(cases),
    )


async def caseworkspace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await case_workspace_menu(update, context)
        return
    cases = search_cases(query, 2)
    if not cases:
        await update.effective_message.reply_text(f"❌ Case not found: {query}")
        return
    if len(cases) > 1:
        await update.effective_message.reply_text(
            "More than one case matched. Select one:",
            reply_markup=cases_keyboard(cases),
        )
        return
    case = cases[0]
    await update.effective_message.reply_text(
        render_workspace(case),
        parse_mode=ParseMode.HTML,
        reply_markup=workspace_keyboard(case.db_id),
    )


async def case_workspace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "list"

    if action == "dashboard":
        await query.edit_message_text("🏠 Tap the persistent Dashboard button below.")
        return

    if action == "search_help":
        await query.edit_message_text(
            "🔎 <b>SEARCH CASES</b>\n\n"
            "Type:\n<code>/casesearch name, case number or mobile</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Cases", callback_data="casews:list")]
            ]),
        )
        return

    if action == "list":
        cases = recent_cases(10)
        await query.edit_message_text(
            "⚖️ <b>CASE WORKSPACE</b>\nSelect a recent case:",
            parse_mode=ParseMode.HTML,
            reply_markup=cases_keyboard(cases),
        )
        return

    if len(parts) < 3 or not parts[2].isdigit():
        await query.edit_message_text("❌ Invalid case selection.")
        return

    db_id = int(parts[2])
    case = get_case(db_id)
    if not case:
        await query.edit_message_text("❌ This case is no longer available.")
        return

    if action == "open":
        await query.edit_message_text(
            render_workspace(case),
            parse_mode=ParseMode.HTML,
            reply_markup=workspace_keyboard(db_id),
        )
        return

    if action == "hearing":
        text = (
            "📅 <b>HEARING</b>\n\n"
            f"Case: {esc(case.case_title)}\n"
            f"Next Hearing: <b>{esc(case.next_hearing)}</b>\n"
            f"Court: {esc(case.court_name)}\n"
            f"Judge: {esc(case.judge_name)}\n\n"
            "The full hearing workflow will remain connected to the Hearings module."
        )
    elif action == "tasks":
        tasks = get_case_tasks(case)
        if tasks:
            lines = ["✅ <b>CASE TASKS</b>", ""]
            for task in tasks:
                status = esc(task.get("status") or "PENDING")
                lines.append(
                    f"<b>#{task['id']}</b> · {status}\n"
                    f"📝 {esc(task.get('task'))}\n"
                    f"👤 {esc(task.get('assigned_to'))}\n"
                    f"📅 {esc(task.get('deadline') or task.get('due_at'))}\n"
                    f"/taskdetails {task['id']}"
                )
                lines.append("──────────")
            text = "\n".join(lines)
        else:
            text = "✅ <b>CASE TASKS</b>\n\nNo linked tasks found."
    elif action == "documents":
        counts = document_counts(case)
        identifier = case.case_number if case.case_number != "-" else case.case_id
        text = (
            "📂 <b>CASE DOCUMENTS</b>\n\n"
            f"🆔 <b>{esc(identifier)}</b>\n"
            f"📄 Total indexed files: <b>{counts.get('TOTAL', 0)}</b>\n\n"
            f"📝 Pleadings: {counts.get('PLEADINGS', 0)}\n"
            f"⚖️ Orders: {counts.get('ORDERS', 0)}\n"
            f"🧾 Evidence: {counts.get('EVIDENCE', 0)}\n"
            f"📚 Judgments: {counts.get('JUDGMENTS', 0)}\n"
            f"✉️ Correspondence: {counts.get('CORRESPONDENCE', 0)}\n"
            f"📎 Miscellaneous: {counts.get('MISCELLANEOUS', 0)}\n\n"
            f"Upload: <code>/upload {esc(identifier)}</code>\n"
            f"Full list: <code>/files {esc(identifier)}</code>"
        )
        rows = [
            [InlineKeyboardButton("➕ Upload New Document", callback_data=f"docupload:choose:{db_id}")],
            [
                InlineKeyboardButton("📄 All Files", callback_data=f"casews:doclist:{db_id}"),
                InlineKeyboardButton("📝 Pleadings", callback_data=f"casews:doccat_PLEADINGS:{db_id}"),
            ],
            [
                InlineKeyboardButton("⚖️ Orders", callback_data=f"casews:doccat_ORDERS:{db_id}"),
                InlineKeyboardButton("🧾 Evidence", callback_data=f"casews:doccat_EVIDENCE:{db_id}"),
            ],
            [
                InlineKeyboardButton("📚 Judgments", callback_data=f"casews:doccat_JUDGMENTS:{db_id}"),
                InlineKeyboardButton("📎 Other", callback_data=f"casews:doccat_MISCELLANEOUS:{db_id}"),
            ],
        ]
        if case.drive_folder_link != "-":
            rows.append([InlineKeyboardButton("☁️ Open Google Drive Folder", url=case.drive_folder_link)])
        rows.extend([
            [InlineKeyboardButton("⬅️ Case Workspace", callback_data=f"casews:open:{db_id}")],
            [InlineKeyboardButton("🏠 Dashboard", callback_data="casews:dashboard")],
        ])
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
        return
    elif action == "doclist":
        documents = list_case_documents(case, limit=20)
        text = render_document_list(case, documents, "RECENT CASE DOCUMENTS")
    elif action.startswith("doccat_"):
        category = action.removeprefix("doccat_")
        documents = list_case_documents(case, category=category, limit=20)
        heading = f"{category.replace('_', ' ').title()} DOCUMENTS"
        text = render_document_list(case, documents, heading)
    elif action == "fees":
        entries = get_fee_installments(case)
        text = (
            "💰 <b>FEES</b>\n\n"
            f"Agreed: {esc(case.fee_agreed)}\n"
            f"Advance: {esc(case.advance_received)}\n"
        )
        if entries:
            text += "\n<b>Installments</b>\n"
            for item in entries:
                text += f"• {esc(item.get('amount'))} · {esc(item.get('date'))}\n"
        else:
            text += "\nNo installment entries found."
    elif action == "notes":
        text = f"📝 <b>CASE NOTES</b>\n\n{esc(case.notes)}"
    elif action == "staff":
        staff = get_case_staff(case)
        if staff:
            lines = ["👥 <b>ASSIGNED STAFF</b>", ""]
            for item in staff:
                lines.append(
                    f"• <b>{esc(item.get('staff_name'))}</b> — "
                    f"{esc(item.get('responsibility'))}"
                )
            text = "\n".join(lines)
        else:
            text = "👥 <b>ASSIGNED STAFF</b>\n\nNo staff responsibility is recorded."
    elif action == "works":
        identifier = case.case_number if case.case_number != "-" else case.case_id
        text = (
            "📋 <b>CASE WORKS</b>\n\n"
            "Advocate Diaries remains the source of truth for Works.\n"
            f"Case reference: <code>{esc(identifier)}</code>\n\n"
            "Use the Works menu to view and assign authoritative pending Works."
        )
    elif action == "timeline":
        events = get_case_timeline(case, limit=40)
        text = render_timeline(case, events)
    else:
        text = "❌ Unknown Case Workspace action."

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(db_id),
        disable_web_page_preview=True,
    )
