"""Private structured ledger, daily closing summary and cash-box UI."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from services.ledger_service import add_entry, cash_box_balance, check_access, ensure_ledger_schema, ledger_summary, parse_amount, soft_delete_entry

WAIT_AMOUNT, WAIT_NOTE, WAIT_REFERENCE = range(3)
logger = logging.getLogger(__name__)

CATEGORY_MAP = {
    ("INCOME", "PROFESSIONAL"): ["Case Fee", "Consultation", "Drafting Fee", "Appearance Fee", "Reimbursement", "Other Professional Income"],
    ("EXPENSE", "PROFESSIONAL"): ["Court Fee", "Filing Charges", "Clerkage", "Photocopy", "Courier", "Travel", "Fuel", "Office Rent", "Electricity", "Internet", "Office Supplies", "Tea & Refreshments", "Other Professional Expense"],
    ("INCOME", "PERSONAL"): ["Personal Receipt", "Investment Return", "Refund", "Gift", "Other Personal Income"],
    ("EXPENSE", "PERSONAL"): ["Grocery", "Food", "Fuel", "Family", "Shopping", "Medical", "Entertainment", "Investment", "EMI", "Other Personal Expense"],
    ("INCOME", "STAFF"): ["Staff Recovery", "Advance Returned", "Other Staff Receipt"],
    ("EXPENSE", "STAFF"): ["Salary", "Advance", "Travel Reimbursement", "Court Expense", "Refreshments", "Bonus", "Other Staff Expenditure"],
}
PAYMENT_MODES = ["CASH", "BANK", "UPI", "CARD"]


def esc(value) -> str:
    return html.escape(str(value or "-"))


def money(value) -> str:
    try:
        return f"₹{Decimal(value or 0):,.2f}"
    except Exception:
        return f"₹{value or 0}"


async def _authorize(update: Update):
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("🔒 The financial ledger is available only in a private chat with the bot.")
        return None
    try:
        access = await asyncio.to_thread(check_access, update.effective_user.id)
    except Exception as exc:
        logger.exception("Ledger access check failed for Telegram user %s", update.effective_user.id)
        await update.effective_message.reply_text(
            f"⚠️ Ledger access could not be checked ({type(exc).__name__}). Please try again or ask Ajay to review the Railway log."
        )
        return None
    if not access.allowed:
        await update.effective_message.reply_text(
            "⛔ Ledger access denied.\n\n"
            f"Reason: {access.reason or 'This account is not authorised.'}\n"
            f"Telegram User ID: {update.effective_user.id}"
        )
        return None
    return access


def ledger_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Income", callback_data="ledger:add:INCOME"), InlineKeyboardButton("➖ Expense", callback_data="ledger:add:EXPENSE")],
        [InlineKeyboardButton("📅 Today", callback_data="ledger:view:today"), InlineKeyboardButton("🗓 This Month", callback_data="ledger:view:month")],
        [InlineKeyboardButton("💵 Cash Box", callback_data="ledger:view:cashbox"), InlineKeyboardButton("📊 Daily Closing", callback_data="ledger:view:closing")],
        [InlineKeyboardButton("💼 Professional", callback_data="ledger:view:professional"), InlineKeyboardButton("🏠 Personal", callback_data="ledger:view:personal")],
        [InlineKeyboardButton("👥 Staff Expenditure", callback_data="ledger:view:staff")],
    ])


def scope_keyboard(entry_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💼 Professional", callback_data=f"ledger:scope:{entry_type}:PROFESSIONAL"), InlineKeyboardButton("🏠 Personal", callback_data=f"ledger:scope:{entry_type}:PERSONAL")],
        [InlineKeyboardButton("👥 Staff", callback_data=f"ledger:scope:{entry_type}:STAFF")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ledger:cancel")],
    ])


def category_keyboard(entry_type: str, scope: str) -> InlineKeyboardMarkup:
    rows = []
    for idx, label in enumerate(CATEGORY_MAP[(entry_type, scope)]):
        rows.append([InlineKeyboardButton(label, callback_data=f"ledger:category:{idx}")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="ledger:cancel")])
    return InlineKeyboardMarkup(rows)


def payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Cash", callback_data="ledger:mode:CASH"), InlineKeyboardButton("🏦 Bank", callback_data="ledger:mode:BANK")],
        [InlineKeyboardButton("📱 UPI", callback_data="ledger:mode:UPI"), InlineKeyboardButton("💳 Card", callback_data="ledger:mode:CARD")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ledger:cancel")],
    ])


def render_summary(title: str, summary: dict, scope_filter: str | None = None) -> str:
    income = Decimal(summary.get("income") or 0)
    expense = Decimal(summary.get("expense") or 0)
    lines = [
        f"💰 <b>{esc(title)}</b>", "",
        f"📥 Income: <b>{money(income)}</b>",
        f"📤 Expenditure: <b>{money(expense)}</b>",
        f"🏠 Personal: {money(summary.get('personal_expense'))}",
        f"💼 Professional: {money(summary.get('professional_expense'))}",
        f"👥 Staff: {money(summary.get('staff_expense'))}",
        f"🧮 Net: <b>{money(income-expense)}</b>",
        f"🧾 Entries: {summary.get('entries', 0)}", "",
    ]
    shown = 0
    for row in summary.get("rows", []):
        if scope_filter and row.get("scope") != scope_filter:
            continue
        shown += 1
        icon = "📥" if row.get("entry_type") == "INCOME" else "📤"
        ref = row.get("case_number") or row.get("staff_name") or ""
        lines.append(
            f"{icon} <b>#{row['id']} · {money(row['amount'])}</b> · {esc(row.get('payment_mode') or 'CASH')}\n"
            f"{esc(row.get('scope'))} · {esc(row.get('category'))}\n{esc(row.get('description'))}"
            + (f"\n🔗 {esc(ref)}" if ref else "")
            + f"\n👤 {esc(row.get('created_by_name'))} · {esc(row.get('entry_date'))}\n/deleteledger {row['id']}"
        )
        lines.append("──────────")
        if shown >= 15:
            break
    if shown == 0:
        lines.append("No matching ledger entries found.")
    return "\n".join(lines)


def render_cashbox() -> str:
    balances = cash_box_balance()
    return (
        "💵 <b>CASH BOX & ACCOUNT LEDGER</b>\n\n"
        f"Cash in Hand: <b>{money(balances['cash_balance'])}</b>\n"
        f"Bank/UPI Ledger Balance: <b>{money(balances['bank_ledger_balance'])}</b>\n"
        f"Overall Ledger Balance: <b>{money(balances['overall_balance'])}</b>\n\n"
        "These are balances derived from ledger entries, not live bank balances.\n"
        "To establish the starting cash or bank figure, record an income entry under an opening-balance category."
    )


def render_closing() -> str:
    summary = ledger_summary(date.today(), date.today())
    balances = cash_box_balance()
    return (
        "📊 <b>DAILY CLOSING SUMMARY</b>\n\n"
        f"Professional/Other Income: <b>{money(summary.get('income'))}</b>\n"
        f"Professional Expenses: {money(summary.get('professional_expense'))}\n"
        f"Personal Expenses: {money(summary.get('personal_expense'))}\n"
        f"Staff Expenditure: {money(summary.get('staff_expense'))}\n"
        f"Cash Received Today: {money(summary.get('cash_income'))}\n"
        f"Cash Paid Today: {money(summary.get('cash_expense'))}\n"
        f"Cash in Hand: <b>{money(balances['cash_balance'])}</b>\n"
        f"Bank/UPI Ledger Balance: <b>{money(balances['bank_ledger_balance'])}</b>\n"
        f"Today's Net: <b>{money(Decimal(summary.get('income') or 0)-Decimal(summary.get('expense') or 0))}</b>"
    )


async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize(update):
        return
    try:
        await asyncio.to_thread(ensure_ledger_schema)
        summary = await asyncio.to_thread(ledger_summary, date.today(), date.today())
        await update.effective_message.reply_text(
            render_summary("DAILY LEDGER · TODAY", summary),
            parse_mode=ParseMode.HTML,
            reply_markup=ledger_keyboard(),
        )
    except Exception as exc:
        logger.exception("Ledger failed for Telegram user %s", update.effective_user.id)
        await update.effective_message.reply_text(
            f"⚠️ Ledger could not be opened ({type(exc).__name__}). The error has been logged for review."
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
        await query.edit_message_text("Select ledger scope:", reply_markup=scope_keyboard(entry_type))
        return ConversationHandler.END
    if action == "scope":
        entry_type, scope = parts[2], parts[3]
        context.user_data["ledger_entry"] = {"entry_type": entry_type, "scope": scope}
        await query.edit_message_text("Select a standard category:", reply_markup=category_keyboard(entry_type, scope))
        return ConversationHandler.END
    if action == "category":
        item = context.user_data.get("ledger_entry") or {}
        choices = CATEGORY_MAP.get((item.get("entry_type"), item.get("scope")), [])
        idx = int(parts[2])
        if idx >= len(choices):
            await query.edit_message_text("Invalid category. Start again with /ledger.")
            return ConversationHandler.END
        item["category"] = choices[idx]
        await query.edit_message_text("Enter amount in rupees.\nExample: <code>12500</code>", parse_mode=ParseMode.HTML)
        return WAIT_AMOUNT
    if action == "mode":
        item = context.user_data.get("ledger_entry") or {}
        mode = parts[2]
        if mode not in PAYMENT_MODES:
            return ConversationHandler.END
        item["payment_mode"] = mode
        await query.edit_message_text("Enter a short note/description.\nExample: <code>Pakodas during court lunch</code>", parse_mode=ParseMode.HTML)
        return WAIT_NOTE
    if action == "cancel":
        context.user_data.pop("ledger_entry", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    view = parts[-1]
    if view == "cashbox":
        text = render_cashbox()
    elif view == "closing":
        text = render_closing()
    else:
        start = date.today().replace(day=1) if view == "month" else date.today()
        scope = None
        title = "LEDGER · THIS MONTH" if view == "month" else "DAILY LEDGER · TODAY"
        if view == "professional": scope, title = "PROFESSIONAL", "PROFESSIONAL LEDGER · TODAY"
        elif view == "personal": scope, title = "PERSONAL", "PERSONAL LEDGER · TODAY"
        elif view == "staff": scope, title = "STAFF", "STAFF EXPENDITURE · TODAY"
        text = render_summary(title, ledger_summary(start, date.today()), scope)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=ledger_keyboard())
    return ConversationHandler.END


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return ConversationHandler.END
    try:
        amount = parse_amount(update.effective_message.text)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}\nEnter the amount again:")
        return WAIT_AMOUNT
    context.user_data.setdefault("ledger_entry", {})["amount"] = str(amount)
    await update.effective_message.reply_text("Select payment mode:", reply_markup=payment_keyboard())
    return ConversationHandler.END


async def note_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if len(text) < 2:
        await update.effective_message.reply_text("Please enter a meaningful note.")
        return WAIT_NOTE
    context.user_data.setdefault("ledger_entry", {})["description"] = text
    await update.effective_message.reply_text(
        "Enter optional reference, or type <code>skip</code>.\n\n"
        "Professional: case number\nStaff expenditure: staff name",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_REFERENCE


async def reference_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await _authorize(update)
    if not access:
        return ConversationHandler.END
    item = context.user_data.get("ledger_entry") or {}
    required = {"entry_type", "scope", "category", "amount", "payment_mode", "description"}
    if not required.issubset(item):
        await update.effective_message.reply_text("Ledger session expired. Start again with /ledger.")
        return ConversationHandler.END
    reference = (update.effective_message.text or "").strip()
    kwargs = {}
    if reference.casefold() != "skip":
        if item["scope"] == "STAFF": kwargs["staff_name"] = reference
        else: kwargs["case_number"] = reference
    entry_id = add_entry(
        entry_type=item["entry_type"], scope=item["scope"], category=item["category"],
        amount=parse_amount(item["amount"]), description=item["description"],
        payment_mode=item["payment_mode"], actor_id=update.effective_user.id,
        actor_name=access.actor_name, **kwargs,
    )
    context.user_data.pop("ledger_entry", None)
    await update.effective_message.reply_text(
        f"✅ Ledger entry #{entry_id} saved.\n{item['entry_type']} · {item['scope']} · {item['category']}\n{money(item['amount'])} · {item['payment_mode']}",
        reply_markup=ledger_keyboard(),
    )
    return ConversationHandler.END


async def cancel_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ledger_entry", None)
    await update.effective_message.reply_text("Ledger entry cancelled.")
    return ConversationHandler.END


async def deleteledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /deleteledger ENTRY_ID")
        return
    deleted = soft_delete_entry(int(context.args[0]), update.effective_user.id)
    await update.effective_message.reply_text("✅ Ledger entry removed." if deleted else "Entry not found or already removed.")


async def cashbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    await update.effective_message.reply_text(render_cashbox(), parse_mode=ParseMode.HTML, reply_markup=ledger_keyboard())


async def dailyclosing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    await update.effective_message.reply_text(render_closing(), parse_mode=ParseMode.HTML, reply_markup=ledger_keyboard())


async def case_fee_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE, case) -> None:
    if not await _authorize(update):
        return
    identifier = case.case_number if case.case_number != "-" else case.case_id
    text = render_summary(f"CASE FEE LEDGER · {identifier}", ledger_summary(date(2000, 1, 1), date.today(), identifier))
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Fee Received", callback_data="ledger:add:INCOME"), InlineKeyboardButton("➖ Case Expense", callback_data="ledger:add:EXPENSE")],
        [InlineKeyboardButton("⬅️ Case Workspace", callback_data=f"casews:open:{case.db_id}")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def build_ledger_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ledger_callback, pattern=r"^ledger:category:"),
            CallbackQueryHandler(ledger_callback, pattern=r"^ledger:mode:"),
        ],
        states={
            WAIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            WAIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, note_received)],
            WAIT_REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reference_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_ledger)],
        per_chat=True, per_user=True, allow_reentry=True,
    )


def register_ledger_handlers(app) -> None:
    ensure_ledger_schema()
    app.add_handler(CommandHandler("ledger", ledger), group=-10)
    app.add_handler(CommandHandler("dailyledger", ledger), group=-10)
    app.add_handler(CommandHandler("cashbox", cashbox), group=-10)
    app.add_handler(CommandHandler("dailyclosing", dailyclosing), group=-10)
    app.add_handler(CommandHandler("deleteledger", deleteledger), group=-10)
    app.add_handler(CallbackQueryHandler(ledger_callback, pattern=r"^ledger:(add|scope|view|cancel):"), group=-10)
    app.add_handler(build_ledger_conversation_handler(), group=-10)
