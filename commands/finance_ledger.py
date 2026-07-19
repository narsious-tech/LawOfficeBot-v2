"""Private fees and daily ledger UI for Ajay and Preet only."""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from services.ledger_service import (
    add_entry,
    check_access,
    ensure_ledger_schema,
    ledger_summary,
    parse_amount,
    soft_delete_entry,
)

WAIT_AMOUNT, WAIT_DESCRIPTION, WAIT_REFERENCE = range(3)


def esc(value) -> str:
    return html.escape(str(value or "-"))


def money(value) -> str:
    try:
        return f"₹{Decimal(value or 0):,.2f}"
    except Exception:
        return f"₹{value or 0}"


async def _authorize(update: Update):
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("🔒 The financial ledger can be used only in a private chat with the bot.")
        return None
    access = check_access(update.effective_user.id)
    if not access.allowed:
        await update.effective_message.reply_text("⛔ Access denied. This ledger can be viewed and updated only by Ajay and Preet.")
        return None
    return access


def ledger_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Income", callback_data="ledger:add:INCOME"),
            InlineKeyboardButton("➖ Expense", callback_data="ledger:add:EXPENSE"),
        ],
        [
            InlineKeyboardButton("📅 Today", callback_data="ledger:view:today"),
            InlineKeyboardButton("🗓 This Month", callback_data="ledger:view:month"),
        ],
        [InlineKeyboardButton("💼 Professional", callback_data="ledger:view:professional")],
        [InlineKeyboardButton("🏠 Personal", callback_data="ledger:view:personal")],
        [InlineKeyboardButton("👥 Staff Expenditure", callback_data="ledger:view:staff")],
    ])


def scope_keyboard(entry_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💼 Professional", callback_data=f"ledger:scope:{entry_type}:PROFESSIONAL"),
            InlineKeyboardButton("🏠 Personal", callback_data=f"ledger:scope:{entry_type}:PERSONAL"),
        ],
        [InlineKeyboardButton("👥 Staff", callback_data=f"ledger:scope:{entry_type}:STAFF")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ledger:cancel")],
    ])


def render_summary(title: str, summary: dict, scope_filter: str | None = None) -> str:
    income = Decimal(summary.get("income") or 0)
    expense = Decimal(summary.get("expense") or 0)
    balance = income - expense
    lines = [
        f"💰 <b>{esc(title)}</b>", "",
        f"📥 Earnings / Fees Received: <b>{money(income)}</b>",
        f"📤 Total Expenditure: <b>{money(expense)}</b>",
        f"🏠 Personal Expenses: {money(summary.get('personal_expense'))}",
        f"💼 Professional Expenses: {money(summary.get('professional_expense'))}",
        f"👥 Staff Expenditure: {money(summary.get('staff_expense'))}",
        f"🧮 Net Balance: <b>{money(balance)}</b>",
        f"🧾 Entries: {summary.get('entries', 0)}", "",
    ]
    rows = summary.get("rows", [])
    shown = 0
    for row in rows:
        if scope_filter and row.get("scope") != scope_filter:
            continue
        shown += 1
        icon = "📥" if row.get("entry_type") == "INCOME" else "📤"
        ref = row.get("case_number") or row.get("staff_name") or ""
        lines.append(
            f"{icon} <b>#{row['id']} · {money(row['amount'])}</b>\n"
            f"{esc(row.get('scope'))} · {esc(row.get('category'))}\n"
            f"{esc(row.get('description'))}"
            + (f"\n🔗 {esc(ref)}" if ref else "")
            + f"\n👤 {esc(row.get('created_by_name'))} · {esc(row.get('entry_date'))}\n"
            f"/deleteledger {row['id']}"
        )
        lines.append("──────────")
        if shown >= 15:
            break
    if shown == 0:
        lines.append("No matching ledger entries found.")
    return "\n".join(lines)


async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    access = await _authorize(update)
    if not access:
        return
    ensure_ledger_schema()
    summary = ledger_summary(date.today(), date.today())
    await update.effective_message.reply_text(
        render_summary("DAILY LEDGER · TODAY", summary),
        parse_mode=ParseMode.HTML,
        reply_markup=ledger_keyboard(),
    )


async def ledger_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    access = await _authorize(update)
    if not access:
        return ConversationHandler.END
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "add":
        entry_type = parts[2]
        context.user_data["ledger_entry"] = {"entry_type": entry_type}
        await query.edit_message_text(
            f"{'📥 INCOME' if entry_type == 'INCOME' else '📤 EXPENSE'}\n\nSelect ledger scope:",
            reply_markup=scope_keyboard(entry_type),
        )
        return ConversationHandler.END

    if action == "scope":
        entry_type, scope = parts[2], parts[3]
        context.user_data["ledger_entry"] = {"entry_type": entry_type, "scope": scope}
        await query.edit_message_text("Enter amount in rupees.\nExample: <code>12500</code>", parse_mode=ParseMode.HTML)
        return WAIT_AMOUNT

    if action == "cancel":
        context.user_data.pop("ledger_entry", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    start = date.today().replace(day=1) if parts[-1] == "month" else date.today()
    summary = ledger_summary(start, date.today())
    scope = None
    title = "DAILY LEDGER · TODAY"
    if parts[-1] == "month":
        title = "LEDGER · THIS MONTH"
    elif parts[-1] == "professional":
        scope, title = "PROFESSIONAL", "PROFESSIONAL LEDGER · TODAY"
    elif parts[-1] == "personal":
        scope, title = "PERSONAL", "PERSONAL LEDGER · TODAY"
    elif parts[-1] == "staff":
        scope, title = "STAFF", "STAFF EXPENDITURE · TODAY"
    await query.edit_message_text(
        render_summary(title, summary, scope),
        parse_mode=ParseMode.HTML,
        reply_markup=ledger_keyboard(),
    )
    return ConversationHandler.END


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await _authorize(update)
    if not access:
        return ConversationHandler.END
    try:
        amount = parse_amount(update.effective_message.text)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}\nEnter the amount again:")
        return WAIT_AMOUNT
    context.user_data.setdefault("ledger_entry", {})["amount"] = str(amount)
    await update.effective_message.reply_text(
        "Enter category and description in one line.\n\n"
        "Examples:\n"
        "<code>Case Fee - Advance received</code>\n"
        "<code>Office Rent - July rent</code>\n"
        "<code>Travel - Court visit</code>",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_DESCRIPTION


async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if len(text) < 3:
        await update.effective_message.reply_text("Please enter a meaningful category and description.")
        return WAIT_DESCRIPTION
    if " - " in text:
        category, description = text.split(" - ", 1)
    else:
        category, description = "General", text
    item = context.user_data.setdefault("ledger_entry", {})
    item["category"] = category[:80]
    item["description"] = description
    await update.effective_message.reply_text(
        "Enter optional reference, or type <code>skip</code>.\n\n"
        "For professional income/expense: case number\n"
        "For staff expenditure: staff name\n"
        "Example: <code>COMA/5541/2026</code> or <code>Priya</code>",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_REFERENCE


async def reference_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await _authorize(update)
    if not access:
        return ConversationHandler.END
    item = context.user_data.get("ledger_entry") or {}
    reference = (update.effective_message.text or "").strip()
    kwargs = {}
    if reference.casefold() != "skip":
        if item.get("scope") == "STAFF":
            kwargs["staff_name"] = reference
        else:
            kwargs["case_number"] = reference
    entry_id = add_entry(
        entry_type=item["entry_type"],
        scope=item["scope"],
        category=item["category"],
        amount=parse_amount(item["amount"]),
        description=item["description"],
        actor_id=update.effective_user.id,
        actor_name=access.actor_name,
        **kwargs,
    )
    context.user_data.pop("ledger_entry", None)
    await update.effective_message.reply_text(
        f"✅ Ledger entry #{entry_id} saved.\n"
        f"{item['entry_type']} · {item['scope']} · {money(item['amount'])}",
        reply_markup=ledger_keyboard(),
    )
    return ConversationHandler.END


async def cancel_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ledger_entry", None)
    await update.effective_message.reply_text("Ledger entry cancelled.")
    return ConversationHandler.END


async def deleteledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await _authorize(update)
    if not access:
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /deleteledger ENTRY_ID")
        return
    deleted = soft_delete_entry(int(context.args[0]), update.effective_user.id)
    await update.effective_message.reply_text("✅ Ledger entry removed." if deleted else "Entry not found or already removed.")


async def case_fee_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE, case) -> None:
    access = await _authorize(update)
    if not access:
        return
    identifier = case.case_number if case.case_number != "-" else case.case_id
    summary = ledger_summary(date(2000, 1, 1), date.today(), identifier)
    text = render_summary(f"CASE FEE LEDGER · {identifier}", summary)
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Fee Received", callback_data="ledger:add:INCOME"),
            InlineKeyboardButton("➖ Case Expense", callback_data="ledger:add:EXPENSE"),
        ],
        [InlineKeyboardButton("⬅️ Case Workspace", callback_data=f"casews:open:{case.db_id}")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def build_ledger_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(ledger_callback, pattern=r"^ledger:scope:")],
        states={
            WAIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            WAIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            WAIT_REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reference_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_ledger)],
        per_chat=True,
        per_user=True,
        allow_reentry=True,
    )


def register_ledger_handlers(app) -> None:
    ensure_ledger_schema()
    app.add_handler(CommandHandler("ledger", ledger), group=-2)
    app.add_handler(CommandHandler("dailyledger", ledger), group=-2)
    app.add_handler(CommandHandler("deleteledger", deleteledger), group=-2)
    app.add_handler(CallbackQueryHandler(ledger_callback, pattern=r"^ledger:(add|view|cancel):"), group=-2)
    app.add_handler(build_ledger_conversation_handler(), group=-2)
