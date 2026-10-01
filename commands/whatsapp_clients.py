"""Private Telegram client-number registry for Ajay."""
from __future__ import annotations

import asyncio
import html
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from services.whatsapp_client_registry import (
    ensure_client_registry_schema,
    list_registered_clients,
    mask_phone,
    mobile_sync_conflicts,
    registered_client_detail,
)


def _is_admin_private(update: Update) -> bool:
    raw = os.getenv("ADMIN_USER_ID", "").strip()
    user_id = update.effective_user.id if update.effective_user else None
    return bool(
        raw.lstrip("-").isdigit()
        and user_id is not None
        and int(user_id) == int(raw)
        and update.effective_chat
        and update.effective_chat.type == ChatType.PRIVATE
    )


def _source_label(row: dict) -> str:
    labels = {
        "ADVOCATE_DIARIES": "Advocate Diaries",
        "MANUAL_CONTACT": "Manual client contact",
        "CLIENT_RECORD": "Office OS client",
        "CASE_RECORD": "Case record",
    }
    return labels.get(row.get("primary_source"), "Office OS")


def _list_view(result: dict) -> tuple[str, InlineKeyboardMarkup]:
    search = str(result.get("search") or "")
    lines = [
        "📱 <b>WHATSAPP CLIENT REGISTRY</b>",
        "",
        f"Registered numbers: <b>{result['total']}</b>",
        f"Page: <b>{result['page']} of {result['pages']}</b>",
    ]
    if search:
        lines.append(f"Search: <b>{html.escape(search)}</b>")
    lines.append("")
    buttons = []
    for index, row in enumerate(result["rows"], start=(result["page"] - 1) * 8 + 1):
        name = str(row.get("client_name") or "Unknown Client")
        lines.extend([
            f"{index}. <b>{html.escape(name)}</b>",
            f"   📱 {html.escape(mask_phone(row['phone']))}",
            f"   ⚖️ {int(row.get('case_count') or 0)} linked case(s)",
            f"   🔗 {html.escape(_source_label(row))}",
            "",
        ])
        buttons.append([InlineKeyboardButton(
            f"{index}. {name[:24]} · {row['phone'][-4:]}",
            callback_data=f"wac:v:{row['token']}:{result['page']}",
        )])
    if not result["rows"]:
        lines.append("No registered client number matched this search.")
    nav = []
    if result["page"] > 1:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"wac:p:{result['page'] - 1}"))
    if result["page"] < result["pages"]:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"wac:p:{result['page'] + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("⚠️ Mobile conflicts", callback_data="wac:conflicts")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def _render_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, *, edit: bool) -> None:
    search = str(context.user_data.get("whatsapp_clients_search") or "")
    result = await asyncio.to_thread(
        list_registered_clients, search=search, page=page, page_size=8,
    )
    context.user_data["whatsapp_clients_page"] = result["page"]
    text, markup = _list_view(result)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup,
        )


async def whatsappclients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin_private(update):
        await update.effective_message.reply_text(
            "🔒 The client-number registry is available only in Ajay's private chat."
        )
        return
    context.user_data["whatsapp_clients_search"] = " ".join(context.args).strip()
    await _render_page(update, context, 1, edit=False)


def _format_full_phone(phone: str) -> str:
    return f"+91 {phone[2:7]} {phone[7:]}"


async def whatsappclients_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not _is_admin_private(update):
        await query.answer("Restricted to Ajay's private chat.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    if data.startswith("wac:p:"):
        page = int(data.rsplit(":", 1)[1])
        await _render_page(update, context, page, edit=True)
        return
    if data == "wac:conflicts":
        rows = await asyncio.to_thread(mobile_sync_conflicts, 20)
        lines = ["⚠️ <b>ADVOCATE DIARIES MOBILE CONFLICTS</b>", ""]
        if not rows:
            lines.append("No mobile-number conflicts are pending.")
        for index, row in enumerate(rows, 1):
            lines.extend([
                f"{index}. <b>{html.escape(str(row.get('client_name') or 'Unknown Client'))}</b>",
                f"   Local retained: {html.escape(_format_full_phone(str(row['existing_mobile'])))}",
                f"   Advocate Diaries: {html.escape(_format_full_phone(str(row['advocate_diaries_mobile'])))}",
                f"   AD Client ID: {html.escape(str(row.get('ad_client_id') or '-'))}",
                "",
            ])
        page = int(context.user_data.get("whatsapp_clients_page") or 1)
        await query.edit_message_text(
            "\n".join(lines)[:4000], parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back to registry", callback_data=f"wac:p:{page}")
            ]]),
        )
        return
    if data.startswith("wac:v:"):
        parts = data.split(":")
        token = parts[2]
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        row = await asyncio.to_thread(registered_client_detail, token)
        if not row:
            await query.answer("Client record is no longer available.", show_alert=True)
            return
        lines = [
            "👤 <b>CLIENT WHATSAPP DETAILS</b>", "",
            f"Name: <b>{html.escape(str(row.get('client_name') or 'Unknown Client'))}</b>",
            f"WhatsApp: <code>{html.escape(_format_full_phone(row['phone']))}</code>",
            f"Source: {html.escape(_source_label(row))}",
            f"AD Client ID: {html.escape(str(row.get('ad_client_id') or '-'))}",
            f"AD sync: {html.escape(str(row.get('ad_sync_status') or 'Not recorded'))}",
            f"Linked cases: {int(row.get('case_count') or 0)}", "",
        ]
        for case in (row.get("cases") or [])[:10]:
            lines.append(
                f"• <b>{html.escape(str(case.get('case_number') or '-'))}</b> — "
                f"{html.escape(str(case.get('case_title') or '-'))}"
            )
        remaining = int(row.get("case_count") or 0) - 10
        if remaining > 0:
            lines.append(f"• …and {remaining} more case(s)")
        lines.extend(["", "Full numbers are displayed only in this private owner view."])
        await query.edit_message_text(
            "\n".join(lines)[:4000], parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data=f"wac:p:{page}")
            ]]),
        )


def register_whatsapp_client_registry_handlers(app) -> None:
    ensure_client_registry_schema()
    app.add_handler(CommandHandler("whatsappclients", whatsappclients), group=-8)
    app.add_handler(
        CallbackQueryHandler(whatsappclients_callback, pattern=r"^wac:"), group=-8
    )
