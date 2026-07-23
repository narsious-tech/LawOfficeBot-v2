"""Telegram UI and reminders for the admin-only private loan ledger."""
from __future__ import annotations

import asyncio
import calendar
import html
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

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

from services.loan_ledger_service import (
    add_documents,
    create_loan,
    ensure_loan_schema,
    get_loan,
    interest_alerts,
    is_loan_admin,
    list_loans,
    mark_reminder_sent,
    monthly_interest,
    parse_date,
    parse_money,
    parse_rate,
    reminder_already_sent,
    record_payment,
)

logger = logging.getLogger(__name__)

(
    WAIT_BORROWER,
    WAIT_BORROWER_DETAILS,
    WAIT_PRINCIPAL,
    WAIT_RATE,
    WAIT_LOAN_DATE,
    WAIT_DISBURSEMENT,
    WAIT_FIRST_INTEREST,
    WAIT_MATURITY,
    WAIT_GUARANTOR,
    WAIT_SECURITY,
    WAIT_DOCUMENTS,
    WAIT_NOTES,
    WAIT_PAYMENT_AMOUNT,
    WAIT_PAYMENT_NOTE,
    WAIT_ADD_DOCUMENTS,
) = range(15)


def esc(value: object) -> str:
    return html.escape(str(value or "-"))


def money(value: object) -> str:
    try:
        amount = Decimal(value or 0).quantize(Decimal("0.01"))
        sign = "-" if amount < 0 else ""
        whole, fraction = f"{abs(amount):.2f}".split(".")
        if len(whole) > 3:
            whole = whole[:-3]
            groups = []
            while len(whole) > 2:
                groups.insert(0, whole[-2:])
                whole = whole[:-2]
            if whole:
                groups.insert(0, whole)
            grouped = ",".join(groups) + "," + f"{abs(amount):.2f}".split(".")[0][-3:]
        else:
            grouped = whole
        return f"₹{sign}{grouped}.{fraction}"
    except Exception:
        return f"₹{value or 0}"


async def _authorize(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "🔒 The private loan ledger can be opened only in your private chat with the bot."
        )
        return False
    user_id = update.effective_user.id if update.effective_user else None
    if not is_loan_admin(user_id):
        await update.effective_message.reply_text(
            "⛔ Private loan ledger access denied.\n"
            "Only the configured administrator can open this account."
        )
        return False
    return True


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ New Loan", callback_data="loan:add")],
        [
            InlineKeyboardButton("📒 Active Accounts", callback_data="loan:list"),
            InlineKeyboardButton("🔔 Interest Due", callback_data="loan:due"),
        ],
        [InlineKeyboardButton("📚 All Accounts", callback_data="loan:all")],
        [InlineKeyboardButton("❌ Close", callback_data="loan:cancel")],
    ])


def loan_keyboard(loan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Interest Receipt", callback_data=f"loan:paytype:{loan_id}:INTEREST_RECEIVED"),
            InlineKeyboardButton("🏦 Principal Receipt", callback_data=f"loan:paytype:{loan_id}:PRINCIPAL_RECEIVED"),
        ],
        [InlineKeyboardButton(
            "🧾 Opening Interest Correction",
            callback_data=f"loan:paytype:{loan_id}:OPENING_INTEREST_RECEIVED",
        )],
        [InlineKeyboardButton("📄 Add Documents", callback_data=f"loan:documents:{loan_id}")],
        [InlineKeyboardButton("⬅️ Loan Accounts", callback_data="loan:list")],
    ])


def payment_mode_keyboard(loan_id: int, payment_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Cash", callback_data=f"loan:paymode:{loan_id}:{payment_type}:CASH"),
            InlineKeyboardButton("🏦 Bank", callback_data=f"loan:paymode:{loan_id}:{payment_type}:BANK"),
        ],
        [
            InlineKeyboardButton("📱 UPI", callback_data=f"loan:paymode:{loan_id}:{payment_type}:UPI"),
            InlineKeyboardButton("🧾 Other", callback_data=f"loan:paymode:{loan_id}:{payment_type}:OTHER"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="loan:cancel")],
    ])


def loan_list_keyboard(loans: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for loan in loans:
        label = f"{loan.get('account_number')} • {loan.get('borrower_name')}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"loan:open:{loan['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="loan:home")])
    return InlineKeyboardMarkup(rows)


def render_loan(loan: dict) -> str:
    due = loan["next_interest_due_date"]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    days = (due - today).days
    if days < 0:
        due_status = f"🔴 Overdue by {abs(days)} day(s)"
    elif days == 0:
        due_status = "🟠 Due today"
    else:
        due_status = f"🟢 Due in {days} day(s)"
    lines = [
        f"🏦 <b>{esc(loan.get('account_number'))} · PRIVATE LOAN</b>",
        "",
        f"👤 Borrower: <b>{esc(loan.get('borrower_name'))}</b>",
        f"📱 Phone: {esc(loan.get('borrower_phone'))}",
        f"🏠 Address: {esc(loan.get('borrower_address'))}",
        f"📅 Loan date: {esc(loan.get('loan_date'))}",
        f"💵 Original principal: <b>{money(loan.get('principal_amount'))}</b>",
        f"📌 Outstanding principal: <b>{money(loan.get('outstanding_principal'))}</b>",
        f"📈 Monthly rate: <b>{esc(loan.get('monthly_interest_rate'))}%</b>",
        f"🧮 Current monthly interest: <b>{money(monthly_interest(loan))}</b>",
        f"⏰ Next advance interest: <b>{esc(due)}</b> · {due_status}",
        f"🏁 Maturity: {esc(loan.get('maturity_date') or 'Open-ended')}",
        f"📊 Status: <b>{esc(loan.get('status'))}</b>",
        "",
        f"🤝 Guarantor: {esc(loan.get('guarantor_name') or 'Not recorded')}",
        f"📱 Guarantor phone: {esc(loan.get('guarantor_phone'))}",
        f"🏠 Guarantor address: {esc(loan.get('guarantor_address'))}",
        f"🔐 Security: {esc(loan.get('security_details') or 'Not recorded')}",
        "",
        "📄 <b>DOCUMENTS HELD</b>",
    ]
    documents = loan.get("documents") or []
    lines.extend(f"• {esc(item.get('document_name'))}" for item in documents)
    if not documents:
        lines.append("• No document record entered")
    lines.extend(["", "🧾 <b>RECENT TRANSACTIONS</b>"])
    transactions = loan.get("transactions") or []
    for item in transactions[:10]:
        lines.append(
            f"• {esc(item.get('transaction_date'))} · {esc(item.get('transaction_type'))} "
            f"· <b>{money(item.get('amount'))}</b> · {esc(item.get('payment_mode'))}"
        )
    if not transactions:
        lines.append("• No transactions recorded")
    return "\n".join(lines)


def render_due() -> str:
    items = interest_alerts(datetime.now(ZoneInfo("Asia/Kolkata")).date())
    lines = ["🔔 <b>PRIVATE LOAN INTEREST DUE</b>", ""]
    if not items:
        lines.append("No interest is due within the next three days.")
        return "\n".join(lines)
    for item in items:
        days = item["days_to_due"]
        status = (
            f"Overdue {abs(days)} day(s)" if days < 0
            else ("Due today" if days == 0 else f"Due in {days} day(s)")
        )
        lines.extend([
            f"🏦 <b>{esc(item.get('account_number'))}</b>",
            f"👤 {esc(item.get('borrower_name'))}",
            f"💵 Outstanding: {money(item.get('outstanding_principal'))}",
            f"📈 Monthly interest: {money(item.get('monthly_interest'))}",
            f"⏰ {esc(item.get('next_interest_due_date'))} · <b>{status}</b>",
            f"📌 Interest currently due: <b>{money(item.get('interest_due'))}</b>",
            "──────────",
        ])
    return "\n".join(lines)


async def loanledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return ConversationHandler.END
    await asyncio.to_thread(ensure_loan_schema)
    active = await asyncio.to_thread(list_loans, "ACTIVE", 100)
    total = sum((Decimal(item["outstanding_principal"]) for item in active), Decimal("0"))
    await update.effective_message.reply_text(
        "🏦 <b>PRIVATE LOAN LEDGER</b>\n\n"
        f"Active accounts: <b>{len(active)}</b>\n"
        f"Principal outstanding: <b>{money(total)}</b>\n\n"
        "Reducing-balance interest · monthly rate · payable in advance\n"
        "🔒 Administrator access only",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


async def loan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _authorize(update):
        return ConversationHandler.END
    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action in {"cancel", "home"}:
        context.user_data.pop("loan_entry", None)
        context.user_data.pop("loan_payment", None)
        if action == "cancel":
            await query.edit_message_text("Private loan ledger closed.")
        else:
            active = await asyncio.to_thread(list_loans, "ACTIVE", 100)
            await query.edit_message_text(
                f"🏦 PRIVATE LOAN LEDGER\n\nActive accounts: {len(active)}",
                reply_markup=main_keyboard(),
            )
        return ConversationHandler.END
    if action == "add":
        context.user_data["loan_entry"] = {}
        await query.edit_message_text("Enter the borrower’s full name:")
        return WAIT_BORROWER
    if action in {"list", "all"}:
        loans = await asyncio.to_thread(list_loans, "ACTIVE" if action == "list" else None, 50)
        await query.edit_message_text(
            "📒 Select a loan account:" if loans else "No loan accounts found.",
            reply_markup=loan_list_keyboard(loans) if loans else main_keyboard(),
        )
        return ConversationHandler.END
    if action == "due":
        text = await asyncio.to_thread(render_due)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())
        return ConversationHandler.END
    if action == "open":
        loan = await asyncio.to_thread(get_loan, int(parts[2]))
        if not loan:
            await query.edit_message_text("Loan account not found.", reply_markup=main_keyboard())
            return ConversationHandler.END
        await query.edit_message_text(
            render_loan(loan), parse_mode=ParseMode.HTML, reply_markup=loan_keyboard(int(parts[2]))
        )
        return ConversationHandler.END
    if action == "paytype":
        loan_id, payment_type = int(parts[2]), parts[3]
        context.user_data["loan_payment"] = {"loan_id": loan_id, "payment_type": payment_type}
        await query.edit_message_text("Enter the amount received:")
        return WAIT_PAYMENT_AMOUNT
    if action == "paymode":
        loan_id, payment_type, mode = int(parts[2]), parts[3], parts[4]
        payment = context.user_data.setdefault("loan_payment", {})
        payment.update({"loan_id": loan_id, "payment_type": payment_type, "payment_mode": mode})
        await query.edit_message_text(
            "Enter receipt/reference details, or type <code>skip</code>:",
            parse_mode=ParseMode.HTML,
        )
        return WAIT_PAYMENT_NOTE
    if action == "documents":
        context.user_data["loan_document_id"] = int(parts[2])
        await query.edit_message_text(
            "Enter documents received, separated by commas.\n"
            "Example: Promissory note, Aadhaar copy, Security cheque"
        )
        return WAIT_ADD_DOCUMENTS
    return ConversationHandler.END


async def borrower_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if len(text) < 2:
        await update.effective_message.reply_text("Enter a valid borrower name:")
        return WAIT_BORROWER
    context.user_data["loan_entry"]["borrower_name"] = text
    await update.effective_message.reply_text(
        "Enter borrower phone and address as:\n<code>PHONE | ADDRESS</code>\n\nType <code>skip</code> if not available.",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_BORROWER_DETAILS


async def borrower_details_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    item = context.user_data["loan_entry"]
    if text.casefold() != "skip":
        parts = [part.strip() for part in text.split("|", 1)]
        item["borrower_phone"] = parts[0] or None
        item["borrower_address"] = parts[1] if len(parts) > 1 else None
    await update.effective_message.reply_text("Enter principal loan amount:")
    return WAIT_PRINCIPAL


async def principal_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["loan_entry"]["principal"] = parse_money(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}\nEnter principal again:")
        return WAIT_PRINCIPAL
    await update.effective_message.reply_text(
        "Enter monthly interest rate percentage.\nExample: <code>1.5</code> for 1.5% per month.",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_RATE


async def rate_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["loan_entry"]["monthly_rate"] = parse_rate(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}\nEnter rate again:")
        return WAIT_RATE
    await update.effective_message.reply_text("Enter loan date in DD-MM-YYYY:")
    return WAIT_LOAN_DATE


async def loan_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = parse_date(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
        return WAIT_LOAN_DATE
    context.user_data["loan_entry"]["loan_date"] = value
    await update.effective_message.reply_text(
        "Enter disbursement details as:\n"
        "<code>MODE | REFERENCE</code>\n\n"
        "Modes: CASH, BANK, UPI, CHEQUE or OTHER.\n"
        "Example: <code>BANK | UTR 123456</code>"
        "\nType <code>skip</code> if not recorded.",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_DISBURSEMENT


async def disbursement_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    item = context.user_data["loan_entry"]
    if text.casefold() == "skip":
        item["disbursement_mode"] = "NOT_RECORDED"
        item["disbursement_reference"] = None
    else:
        parts = [part.strip() for part in text.split("|", 1)]
        mode = parts[0].upper()
        if mode not in {"CASH", "BANK", "UPI", "CHEQUE", "OTHER"}:
            await update.effective_message.reply_text(
                "❌ Use CASH, BANK, UPI, CHEQUE or OTHER, followed by | reference."
            )
            return WAIT_DISBURSEMENT
        item["disbursement_mode"] = mode
        item["disbursement_reference"] = parts[1] if len(parts) > 1 else None
    await update.effective_message.reply_text(
        "Was the first month’s advance interest collected at disbursement?\n"
        "Reply <code>yes</code> or <code>no</code>.\n\n"
        "The next interest due date will be calculated automatically.",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_FIRST_INTEREST


async def first_interest_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip().casefold()
    if text not in {"yes", "y", "no", "n"}:
        await update.effective_message.reply_text(
            "Reply <code>yes</code> if collected, or <code>no</code> if still due.",
            parse_mode=ParseMode.HTML,
        )
        return WAIT_FIRST_INTEREST
    context.user_data["loan_entry"]["first_interest_collected"] = text in {"yes", "y"}
    await update.effective_message.reply_text(
        "Enter maturity date in DD-MM-YYYY, or type <code>open</code> for no fixed maturity:",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_MATURITY


async def maturity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text.casefold() != "open":
        try:
            maturity = parse_date(text)
        except ValueError as exc:
            await update.effective_message.reply_text(f"❌ {exc}")
            return WAIT_MATURITY
        if maturity < context.user_data["loan_entry"]["loan_date"]:
            await update.effective_message.reply_text(
                "❌ Maturity date cannot be before the loan date."
            )
            return WAIT_MATURITY
        context.user_data["loan_entry"]["maturity_date"] = maturity
    await update.effective_message.reply_text(
        "Enter guarantor as <code>NAME | PHONE | ADDRESS</code>, or type <code>skip</code>:",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_GUARANTOR


async def guarantor_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    item = context.user_data["loan_entry"]
    if text.casefold() != "skip":
        parts = [part.strip() for part in text.split("|", 2)]
        item["guarantor_name"] = parts[0] or None
        item["guarantor_phone"] = parts[1] if len(parts) > 1 else None
        item["guarantor_address"] = parts[2] if len(parts) > 2 else None
    await update.effective_message.reply_text(
        "Enter security/collateral details, or type <code>skip</code>:",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_SECURITY


async def security_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text.casefold() != "skip":
        context.user_data["loan_entry"]["security_details"] = text
    await update.effective_message.reply_text(
        "Enter documents received, separated by commas, or type <code>skip</code>.\n"
        "Example: Loan agreement, Promissory note, Aadhaar copy, Security cheque",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_DOCUMENTS


async def documents_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    documents = [] if text.casefold() == "skip" else [item.strip() for item in text.split(",") if item.strip()]
    context.user_data["loan_entry"]["documents"] = documents
    await update.effective_message.reply_text(
        "Enter any private notes, or type <code>skip</code>:",
        parse_mode=ParseMode.HTML,
    )
    return WAIT_NOTES


async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    item = context.user_data.get("loan_entry") or {}
    if text.casefold() != "skip":
        item["notes"] = text
    try:
        loan_id = await asyncio.to_thread(
            create_loan,
            borrower_name=item["borrower_name"],
            borrower_phone=item.get("borrower_phone"),
            borrower_address=item.get("borrower_address"),
            principal=item["principal"],
            monthly_rate=item["monthly_rate"],
            loan_date=item["loan_date"],
            disbursement_mode=item.get("disbursement_mode", "NOT_RECORDED"),
            disbursement_reference=item.get("disbursement_reference"),
            first_interest_collected=bool(item.get("first_interest_collected")),
            maturity_date=item.get("maturity_date"),
            guarantor_name=item.get("guarantor_name"),
            guarantor_phone=item.get("guarantor_phone"),
            guarantor_address=item.get("guarantor_address"),
            security_details=item.get("security_details"),
            notes=item.get("notes"),
            documents=item.get("documents", []),
            actor_id=update.effective_user.id,
        )
        loan = await asyncio.to_thread(get_loan, loan_id)
        context.user_data.pop("loan_entry", None)
        await update.effective_message.reply_text(
            "✅ Private loan account created.\n\n" + render_loan(loan),
            parse_mode=ParseMode.HTML,
            reply_markup=loan_keyboard(loan_id),
        )
    except Exception as exc:
        logger.exception("Private loan creation failed")
        await update.effective_message.reply_text(f"❌ Loan could not be saved safely: {type(exc).__name__}")
    return ConversationHandler.END


async def payment_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = parse_money(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}\nEnter amount again:")
        return WAIT_PAYMENT_AMOUNT
    payment = context.user_data.setdefault("loan_payment", {})
    payment["amount"] = amount
    await update.effective_message.reply_text(
        "Select payment mode:",
        reply_markup=payment_mode_keyboard(payment["loan_id"], payment["payment_type"]),
    )
    return ConversationHandler.END


async def payment_note_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return ConversationHandler.END
    payment = context.user_data.get("loan_payment") or {}
    required = {"loan_id", "payment_type", "payment_mode", "amount"}
    if not required.issubset(payment):
        await update.effective_message.reply_text("Payment session expired. Reopen /loanledger.")
        return ConversationHandler.END
    note = (update.effective_message.text or "").strip()
    if note.casefold() == "skip":
        note = "No reference entered"
    try:
        loan = await asyncio.to_thread(
            record_payment,
            loan_id=payment["loan_id"],
            payment_type=payment["payment_type"],
            amount=payment["amount"],
            payment_date=datetime.now(ZoneInfo("Asia/Kolkata")).date(),
            payment_mode=payment["payment_mode"],
            note=note,
            actor_id=update.effective_user.id,
        )
        context.user_data.pop("loan_payment", None)
        await update.effective_message.reply_text(
            "✅ Payment recorded.\n\n" + render_loan(loan),
            parse_mode=ParseMode.HTML,
            reply_markup=loan_keyboard(int(loan["id"])),
        )
    except Exception as exc:
        logger.exception("Private loan payment failed")
        await update.effective_message.reply_text(f"❌ Payment was not saved: {exc}")
    return ConversationHandler.END


async def add_documents_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return ConversationHandler.END
    documents = [item.strip() for item in (update.effective_message.text or "").split(",") if item.strip()]
    if not documents:
        await update.effective_message.reply_text("Enter at least one document name:")
        return WAIT_ADD_DOCUMENTS
    loan_id = context.user_data.get("loan_document_id")
    try:
        count = await asyncio.to_thread(add_documents, int(loan_id), documents, update.effective_user.id)
        loan = await asyncio.to_thread(get_loan, int(loan_id))
        context.user_data.pop("loan_document_id", None)
        await update.effective_message.reply_text(
            f"✅ {count} document record(s) added.\n\n" + render_loan(loan),
            parse_mode=ParseMode.HTML,
            reply_markup=loan_keyboard(int(loan_id)),
        )
    except Exception as exc:
        logger.exception("Private loan document entry failed")
        await update.effective_message.reply_text(f"❌ Documents were not saved: {type(exc).__name__}")
    return ConversationHandler.END


def _reminder_destination() -> int | None:
    preferred = os.getenv("ADMIN_USER_ID", "").strip()
    if preferred.lstrip("-").isdigit():
        return int(preferred)
    for item in os.getenv("AI_ADMIN_USER_IDS", "").split(","):
        item = item.strip()
        if item.isdigit():
            return int(item)
    fallback = os.getenv("ADMIN_CHAT_ID", "").strip()
    return int(fallback) if fallback.lstrip("-").isdigit() else None


async def loan_interest_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    destination = _reminder_destination()
    if destination is None:
        logger.warning("Loan reminders skipped: ADMIN_USER_ID is not configured")
        return
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    try:
        items = await asyncio.to_thread(interest_alerts, today)
        for item in items:
            # Automatic delivery occurs exactly three days before, on the due
            # date, and daily after default. The manual due view remains wider.
            if item["days_to_due"] in (1, 2):
                continue
            already = await asyncio.to_thread(
                reminder_already_sent,
                int(item["id"]),
                item["next_interest_due_date"],
                today,
                item["alert_kind"],
                destination,
            )
            if already:
                continue
            days = item["days_to_due"]
            status = (
                f"🔴 Overdue by {abs(days)} day(s)" if days < 0
                else ("🟠 Due today" if days == 0 else f"🟡 Due in {days} day(s)")
            )
            await context.bot.send_message(
                chat_id=destination,
                text=(
                    "🔔 <b>PRIVATE LOAN INTEREST REMINDER</b>\n\n"
                    f"🏦 {esc(item.get('account_number'))}\n"
                    f"👤 Borrower: <b>{esc(item.get('borrower_name'))}</b>\n"
                    f"💵 Principal outstanding: <b>{money(item.get('outstanding_principal'))}</b>\n"
                    f"📈 Rate: {esc(item.get('monthly_interest_rate'))}% per month\n"
                    f"🧮 Monthly interest: <b>{money(item.get('monthly_interest'))}</b>\n"
                    f"📌 Interest currently due: <b>{money(item.get('interest_due'))}</b>\n"
                    f"📅 Due date: {esc(item.get('next_interest_due_date'))}\n"
                    f"{status}\n\n"
                    "Open /loanledger to record payment."
                ),
                parse_mode=ParseMode.HTML,
            )
            await asyncio.to_thread(
                mark_reminder_sent,
                int(item["id"]),
                item["next_interest_due_date"],
                today,
                item["alert_kind"],
                destination,
            )
    except Exception:
        logger.exception("Automatic private loan reminder failed")


async def testloanreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    text = await asyncio.to_thread(render_due)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cancel_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ("loan_entry", "loan_payment", "loan_document_id"):
        context.user_data.pop(key, None)
    await update.effective_message.reply_text("Private loan-ledger operation cancelled.")
    return ConversationHandler.END


def build_loan_ledger_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("loanledger", loanledger),
            CallbackQueryHandler(loan_callback, pattern=r"^loan:"),
        ],
        states={
            WAIT_BORROWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, borrower_received)],
            WAIT_BORROWER_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, borrower_details_received)],
            WAIT_PRINCIPAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, principal_received)],
            WAIT_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, rate_received)],
            WAIT_LOAN_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, loan_date_received)],
            WAIT_DISBURSEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, disbursement_received)],
            WAIT_FIRST_INTEREST: [MessageHandler(filters.TEXT & ~filters.COMMAND, first_interest_received)],
            WAIT_MATURITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, maturity_received)],
            WAIT_GUARANTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, guarantor_received)],
            WAIT_SECURITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, security_received)],
            WAIT_DOCUMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, documents_received)],
            WAIT_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
            WAIT_PAYMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount_received)],
            WAIT_PAYMENT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_note_received)],
            WAIT_ADD_DOCUMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_documents_received)],
        },
        fallbacks=[CommandHandler("cancelloan", cancel_loan)],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )


def register_loan_ledger_handlers(app) -> None:
    ensure_loan_schema()
    app.add_handler(build_loan_ledger_handler(), group=-9)
    app.add_handler(CommandHandler("testloanreminders", testloanreminders), group=-9)
