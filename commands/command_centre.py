"""Unified, role-aware command centre for the Law Office OS.

This module extends the earlier Sprint 19 Office OS instead of replacing it.
It keeps a single registry for the interactive /menu and searchable
/command(s) directory.  Actual authorization remains enforced by each feature
handler as a second line of defence.
"""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    command: str
    description: str
    category: str
    audience: str = "staff"  # staff, supervisor, admin
    direct: bool = True
    usage: str | None = None


CATEGORIES = {
    "court": ("⚖️", "Court Operations"),
    "cases": ("📂", "Cases & Clients"),
    "work": ("✅", "Tasks & Works"),
    "documents": ("📄", "Documents & Drive"),
    "staff": ("👥", "Staff & Attendance"),
    "accounts": ("💰", "Accounts"),
    "communications": ("💬", "Communications"),
    "ai": ("🧠", "Ajay AI"),
    "admin": ("⚙️", "Admin & System"),
}


ITEMS = (
    # Court operations
    MenuItem("morning", "🌅 Morning Dashboard", "morningdashboard", "Executive morning briefing.", "court"),
    MenuItem("today", "📅 Today's Hearings", "todayhearings", "Hearings listed today.", "court"),
    MenuItem("live", "🔴 Live Hearings", "livehearings", "Live court board and hearing completion.", "court"),
    MenuItem("tomorrow", "🗓 Tomorrow Cause List", "tomorrowcause", "Tomorrow's cause list.", "court"),
    MenuItem("readiness", "🧭 Hearing Readiness", "readiness", "Preparation and file readiness.", "court"),
    MenuItem("causepdf", "🖨 Printable Cause List", "printablecauselist", "Printable cause-list output.", "court", "supervisor"),
    MenuItem("ecourts", "🏛 eCourts Desk", "ecourts", "Reconciliation, orders and review queues.", "court", "admin"),
    MenuItem("ecwork", "🤖 eCourts AI Work", "ecourtswork", "AI work proposals awaiting approval.", "court", "admin"),

    # Cases and clients
    MenuItem("findcase", "🔎 Find Case", "findcase", "Search by case, party or client.", "cases", direct=False, usage="/findcase CASE_OR_PARTY"),
    MenuItem("case", "⚖️ Case Details", "case", "Open a specific case.", "cases", direct=False, usage="/case CASE_NUMBER"),
    MenuItem("workspace", "🗂 Case Workspace", "caseworkspace", "Unified case workspace.", "cases", direct=False, usage="/caseworkspace CASE_NUMBER"),
    MenuItem("pendingcases", "📌 Pending Cases", "pendingcases", "Cases requiring next-date attention.", "cases"),
    MenuItem("newcase", "➕ New Case", "newcase", "Guided new-case workflow.", "cases", "supervisor", direct=False, usage="/newcase"),
    MenuItem("timeline", "🕘 Client Timeline", "clienttimeline", "Case/client procedural timeline.", "cases", direct=False, usage="/clienttimeline CASE_NUMBER"),
    MenuItem("verification", "📱 Client Verification", "pendingclientverification", "Pending client confirmations.", "cases", "supervisor"),

    # Work
    MenuItem("mydashboard", "👤 My Dashboard", "mydashboard", "Personal workload dashboard.", "work"),
    MenuItem("mytasks", "☑️ My Tasks", "mytasks", "Your pending tasks.", "work"),
    MenuItem("myworks", "📋 My Works", "myworks", "Your assigned works.", "work"),
    MenuItem("workboard", "📊 Work Board", "workboard", "Office work overview.", "work", "supervisor"),
    MenuItem("pendingtasks", "⏳ Pending Tasks", "pendingtasks", "Staff-wise pending tasks.", "work", "supervisor"),
    MenuItem("assigntask", "➕ Assign Task", "assigntask", "Create and assign a task.", "work", "supervisor", False, "/assigntask STAFF TASK | DD-MM-YYYY 6:00 PM"),
    MenuItem("workcontrol", "🧑‍💼 Work Control", "workcontrol", "Assignment control and reconciliation.", "work", "supervisor"),

    # Documents
    MenuItem("latestfiles", "🕒 Latest Files", "latestfiles", "Recently uploaded case files.", "documents"),
    MenuItem("files", "📁 Case Files", "files", "Documents linked to a case.", "documents", direct=False, usage="/files CASE_NUMBER"),
    MenuItem("upload", "⬆️ Upload Document", "upload", "Upload to a case Drive folder.", "documents", direct=False, usage="/upload CASE_NUMBER"),
    MenuItem("docsearch", "🔍 Document Search", "docsearch", "Search indexed documents.", "documents", direct=False, usage="/docsearch KEYWORD"),
    MenuItem("casefolder", "☁️ Drive Folder", "casefolder", "Create or open a case folder.", "documents", "supervisor", False, "/casefolder CASE_NUMBER"),
    MenuItem("filesready", "✅ Files Ready", "filesready", "Tomorrow's physical-file readiness.", "documents", "supervisor"),

    # Staff
    MenuItem("checkin", "🟢 Check In", "checkin", "Open attendance check-in.", "staff"),
    MenuItem("checkout", "🔴 Check Out", "checkout", "Record attendance checkout.", "staff"),
    MenuItem("attendanceapp", "📍 Attendance App", "attendanceapp", "Open the attendance web app.", "staff"),
    MenuItem("who", "👥 Who Is In Office", "whoinoffice", "Current office attendance.", "staff"),
    MenuItem("attendtoday", "📊 Today's Attendance", "attendancetoday", "Today's attendance report.", "staff", "supervisor"),
    MenuItem("staffattendance", "📆 Staff Attendance", "staffattendance", "Staff attendance history.", "staff", "supervisor", False, "/staffattendance STAFF"),
    MenuItem("linkedstaff", "🔗 Linked Staff", "linkedstaff", "Review Telegram/staff links.", "staff", "admin"),

    # Accounts
    MenuItem("ledger", "📒 Office Ledger", "ledger", "Income, expenses and cash box.", "accounts", "supervisor"),
    MenuItem("loanledger", "🏦 Private Loan Ledger", "loanledger", "Administrator-only loan accounts.", "accounts", "admin"),
    MenuItem("pendingfees", "🧾 Pending Fees", "pendingfees", "Cases with outstanding fees.", "accounts", "supervisor"),

    # Communications
    MenuItem("emailstatus", "📨 Email Alert Status", "emailalertstatus", "Gmail and Yahoo monitoring health.", "communications", "supervisor"),
    MenuItem("messagehistory", "💬 Message History", "messagehistory", "Client communication history.", "communications", "supervisor"),
    MenuItem("whatsapp", "🟢 WhatsApp Desk", "whatsappstatus", "WhatsApp Cloud API operations.", "communications", "admin"),

    # AI
    MenuItem("aihome", "🧠 Ajay AI", "ai", "Private legal intelligence workspace.", "ai", "admin"),

    # Admin/system
    MenuItem("commands", "📚 Command Directory", "commands", "Search every registered command.", "admin"),
    MenuItem("officestatus", "🏢 Office Status", "officestatus", "Operational status overview.", "admin", "supervisor"),
    MenuItem("sync", "🔄 Sync Cases", "synccases", "Synchronize Advocate Diaries cases.", "admin", "admin"),
    MenuItem("emailtest", "🧪 Test Email Alerts", "testemailalerts", "Run the email monitor now.", "admin", "admin"),
    MenuItem("chatid", "🪪 My Telegram ID", "mychatid", "Show chat and user identifiers.", "admin"),
)

ITEM_BY_KEY = {item.key: item for item in ITEMS}


def _configured_admin_ids() -> set[int]:
    values = (
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("AI_ADMIN_USER_IDS", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    )
    result: set[int] = set()
    for value in values:
        for token in str(value).split(","):
            token = token.strip()
            if token.lstrip("-").isdigit():
                result.add(int(token))
    return result


def _user_level(user_id: int | None) -> tuple[str, str]:
    if user_id is not None and int(user_id) in _configured_admin_ids():
        return "admin", "Administrator"
    if user_id is None:
        return "staff", "Staff"
    try:
        from services.role_intelligence_service import staff_profile

        profile = staff_profile(int(user_id))
        role = str(profile.get("role") or "staff").strip().lower()
        name = str(profile.get("staff_name") or "Staff")
        if role in {"admin", "owner", "principal"}:
            return "admin", name
        if role in {"supervisor", "manager", "senior"} or name.strip().lower() == "priya":
            return "supervisor", name
        return "staff", name
    except Exception:
        return "staff", "Staff"


def _allowed(item: MenuItem, level: str) -> bool:
    rank = {"staff": 0, "supervisor": 1, "admin": 2}
    return rank.get(level, 0) >= rank.get(item.audience, 0)


def _visible_items(level: str, category: str | None = None) -> list[MenuItem]:
    return [
        item for item in ITEMS
        if _allowed(item, level) and (category is None or item.category == category)
    ]


def _home_keyboard(level: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    visible_categories = [
        key for key in CATEGORIES
        if _visible_items(level, key)
    ]
    for index in range(0, len(visible_categories), 2):
        row = []
        for key in visible_categories[index:index + 2]:
            icon, label = CATEGORIES[key]
            row.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"cc:c:{key}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🔎 Find Command", callback_data="cc:search"),
        InlineKeyboardButton("❌ Close", callback_data="cc:close"),
    ])
    return InlineKeyboardMarkup(rows)


def _category_keyboard(level: str, category: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(item.label, callback_data=f"cc:i:{item.key}")]
        for item in _visible_items(level, category)
    ]
    rows.append([
        InlineKeyboardButton("⬅️ Menu", callback_data="cc:home"),
        InlineKeyboardButton("❌ Close", callback_data="cc:close"),
    ])
    return InlineKeyboardMarkup(rows)


def _home_text(level: str, name: str) -> str:
    count = len(_visible_items(level))
    role = {"admin": "Administrator", "supervisor": "Supervisor", "staff": "Staff"}[level]
    return (
        "🏛 <b>LAW OFFICE OS CONTROL CENTRE</b>\n"
        "<i>Sprint 26 · Unified Command System</i>\n\n"
        f"👤 {html.escape(name)} · <b>{role}</b>\n"
        f"🧩 {count} authorised actions available\n\n"
        "Choose a module below. Sensitive modules are automatically hidden "
        "unless your account is authorised."
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.effective_message:
        return
    try:
        level, name = _user_level(update.effective_user.id if update.effective_user else None)
        await update.effective_message.reply_text(
            _home_text(level, name),
            parse_mode=ParseMode.HTML,
            reply_markup=_home_keyboard(level),
        )
    except Exception:
        logger.exception("Unified command centre failed to open")
        await update.effective_message.reply_text(
            "⚠️ The unified menu could not be opened safely.\n"
            "The earlier Office OS remains available through /office."
        )


async def command_directory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    level, _ = _user_level(update.effective_user.id if update.effective_user else None)
    query = " ".join(context.args or []).strip().lower()
    items = _visible_items(level)
    if query:
        items = [
            item for item in items
            if query in item.command.lower()
            or query in item.label.lower()
            or query in item.description.lower()
        ]
    if not items:
        await update.effective_message.reply_text(
            f"No authorised command matched “{query}”.\n\n"
            "Use /commands without a search term or open /menu."
        )
        return
    lines = ["📚 <b>COMMAND DIRECTORY</b>"]
    if query:
        lines.append(f"Search: <code>{html.escape(query)}</code>")
    current = None
    for item in items:
        if item.category != current:
            current = item.category
            icon, label = CATEGORIES[current]
            lines.extend(["", f"{icon} <b>{label.upper()}</b>"])
        usage = item.usage or f"/{item.command}"
        lines.append(f"<code>{html.escape(usage)}</code> — {html.escape(item.description)}")
    lines.extend(["", "Search example: <code>/commands attendance</code>", "Interactive view: /menu"])
    text = "\n".join(lines)
    # The authorised directory can be long; split at category-safe line boundaries.
    while text:
        if len(text) <= 3900:
            chunk, text = text, ""
        else:
            cut = text.rfind("\n", 0, 3900)
            cut = cut if cut > 0 else 3900
            chunk, text = text[:cut], text[cut:].lstrip()
        await update.effective_message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def _edit_or_reply(query, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _invoke_registered_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
) -> bool:
    """Invoke an already-registered simple CommandHandler.

    Conversation entry points remain instruction-only because starting a
    ConversationHandler from another handler would bypass its state tracking.
    """
    for group in sorted(context.application.handlers):
        for handler in context.application.handlers[group]:
            if isinstance(handler, CommandHandler) and command in handler.commands:
                context.args = []
                command_update = SimpleNamespace(
                    effective_message=update.effective_message,
                    message=update.effective_message,
                    effective_user=update.effective_user,
                    effective_chat=update.effective_chat,
                    callback_query=None,
                )
                await handler.callback(command_update, context)
                return True
    return False


async def command_centre_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    user_id = update.effective_user.id if update.effective_user else None
    level, name = _user_level(user_id)

    if action == "close":
        await _edit_or_reply(query, "✅ Command Centre closed.\n\nOpen it anytime with /menu.")
        return
    if action == "home":
        await _edit_or_reply(query, _home_text(level, name), _home_keyboard(level))
        return
    if action == "search":
        await _edit_or_reply(
            query,
            "🔎 <b>FIND A COMMAND</b>\n\n"
            "Send <code>/commands KEYWORD</code>\n\n"
            "Examples:\n"
            "• <code>/commands attendance</code>\n"
            "• <code>/commands document</code>\n"
            "• <code>/commands eCourts</code>",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="cc:home")]]),
        )
        return
    if action == "c" and len(parts) > 2:
        category = parts[2]
        if category not in CATEGORIES or not _visible_items(level, category):
            await query.answer("This module is not available for your account.", show_alert=True)
            return
        icon, label = CATEGORIES[category]
        await _edit_or_reply(
            query,
            f"{icon} <b>{label.upper()}</b>\n\nChoose an action.",
            _category_keyboard(level, category),
        )
        return
    if action != "i" or len(parts) < 3:
        return

    item = ITEM_BY_KEY.get(parts[2])
    if not item or not _allowed(item, level):
        await query.answer("You are not authorised for this action.", show_alert=True)
        return

    if not item.direct:
        usage = item.usage or f"/{item.command}"
        await _edit_or_reply(
            query,
            f"{item.label}\n\n{html.escape(item.description)}\n\n"
            f"Send:\n<code>{html.escape(usage)}</code>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"cc:c:{item.category}")],
                [InlineKeyboardButton("🏛 Main Menu", callback_data="cc:home")],
            ]),
        )
        return

    invoked = await _invoke_registered_command(update, context, item.command)
    if not invoked:
        await query.message.reply_text(
            f"{item.label}\n\nSend <code>/{html.escape(item.command)}</code> to open this feature.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="cc:home")]]),
        )


def register_command_centre(app) -> None:
    # Registered early so the modern /commands directory supersedes the legacy
    # static text handler without removing any legacy command.
    # Use individual handlers for compatibility with every PTB 21.x command
    # normalization path and to make handler registration visible in logs.
    app.add_handler(CommandHandler("menu", menu_command), group=-20)
    app.add_handler(CommandHandler("command", menu_command), group=-20)
    app.add_handler(CommandHandler("commands", command_directory), group=-20)
    app.add_handler(CommandHandler("help", command_directory), group=-20)
    app.add_handler(CallbackQueryHandler(command_centre_callback, pattern=r"^cc:"), group=-20)
