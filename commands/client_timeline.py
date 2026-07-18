from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from services.client_timeline import (
    add_manual_timeline_event,
    backfill_timeline_for_case,
    get_timeline,
)


EVENT_ICONS = {
    "COMMUNICATION": "📨",
    "CLIENT_VERIFIED": "✅",
    "CLIENT_CHANGE_REQUESTED": "✏️",
    "CLIENT_VERIFICATION": "🧾",
    "TASK": "📋",
    "DOCUMENT": "📄",
    "HEARING": "⚖️",
    "MANUAL_NOTE": "📝",
}


CATEGORY_LABELS = {
    "communications": "Communications",
    "hearings": "Hearings",
    "documents": "Documents",
    "tasks": "Tasks",
    "client_updates": "Client Updates",
    "notes": "Notes",
    "other": "Other",
}


def format_event_datetime(value):
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime(
            "%d-%m-%Y %I:%M %p"
        )

    return str(value)


def friendly_date_text(text):
    if not text:
        return text

    lines = []

    for line in str(text).splitlines():
        if line.startswith("Date: "):
            raw = line.replace(
                "Date: ",
                "",
                1
            ).strip()

            try:
                parsed = datetime.strptime(
                    raw[:10],
                    "%Y-%m-%d"
                )

                line = (
                    "📅 Date: "
                    + parsed.strftime(
                        "%d %B %Y"
                    )
                )

            except ValueError:
                pass

        lines.append(line)

    return "\n".join(lines)


async def clienttimeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/clienttimeline CASE_NUMBER [FILTER]\n\n"
            "Filters:\n"
            "all\n"
            "communications\n"
            "hearings\n"
            "documents\n"
            "tasks\n"
            "client_updates\n"
            "notes\n"
            "cancelled\n\n"
            "Examples:\n"
            "/clienttimeline CS/1635/2026\n"
            "/clienttimeline CS/1635/2026 hearings\n"
            "/clienttimeline CS/1635/2026 cancelled"
        )
        return

    case_value = (
        context.args[0]
        .strip()
    )

    raw_filter = (
        context.args[1]
        .strip()
        .lower()
        if len(context.args) > 1
        else "all"
    )

    include_cancelled = (
        raw_filter == "cancelled"
    )

    category = (
        "communications"
        if include_cancelled
        else raw_filter
    )

    try:
        result = get_timeline(
            case_value=case_value,
            category=category,
            include_cancelled=include_cancelled,
            limit=100
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Client timeline could not be loaded:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    case = result["case"]
    items = result["items"]
    counts = result["counts"]

    message = (
        "🕘 CASE ACTIVITY CENTER\n\n"
        f"🔢 Case: "
        f"{case['canonical_case_id']}\n"
        f"👤 Client: "
        f"{case.get('client_name') or '-'}\n"
        f"⚖️ Title: "
        f"{case.get('case_title') or '-'}\n\n"
        f"📌 Visible Events: {len(items)}\n"
        f"🔎 Filter: {raw_filter}\n\n"
        "📊 ACTIVITY SUMMARY\n"
        f"📨 Communications: "
        f"{counts.get('communications', 0)}\n"
        f"⚖️ Hearings: "
        f"{counts.get('hearings', 0)}\n"
        f"📄 Documents: "
        f"{counts.get('documents', 0)}\n"
        f"📋 Tasks: "
        f"{counts.get('tasks', 0)}\n"
        f"👤 Client Updates: "
        f"{counts.get('client_updates', 0)}\n"
        f"📝 Notes: "
        f"{counts.get('notes', 0)}\n\n"
    )

    if not items:
        message += (
            "No timeline events match this filter.\n\n"
            f"Run /synctimeline "
            f"{case['canonical_case_id']}"
        )

    else:
        for item in items:
            icon = EVENT_ICONS.get(
                item["event_type"],
                "•"
            )

            message += (
                f"{icon} "
                f"{item['event_title']}\n"
                f"🕒 "
                f"{format_event_datetime(item['event_at'])}\n"
            )

            if item.get("event_status"):
                message += (
                    f"📊 Status: "
                    f"{item['event_status']}\n"
                )

            if item.get("event_details"):
                details = friendly_date_text(
                    str(
                        item[
                            "event_details"
                        ]
                    ).strip()
                )

                if len(details) > 700:
                    details = (
                        details[:697]
                        + "..."
                    )

                message += (
                    f"{details}\n"
                )

            message += (
                "──────────────\n\n"
            )

    while message:
        if len(message) <= 3800:
            chunk = message
            message = ""

        else:
            split_at = message.rfind(
                "\n\n",
                0,
                3800
            )

            if split_at == -1:
                split_at = 3800

            chunk = message[:split_at]

            message = message[
                split_at:
            ].lstrip()

        await update.effective_message.reply_text(
            chunk,
            disable_web_page_preview=True
        )


async def synctimeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/synctimeline CASE_NUMBER"
        )
        return

    case_value = (
        context.args[0]
        .strip()
    )

    try:
        counts = (
            backfill_timeline_for_case(
                case_value
            )
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Timeline sync failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ CASE ACTIVITY CENTER SYNCED\n\n"
        f"🔢 Case: {case_value}\n"
        f"📨 Communications updated: "
        f"{counts['messages']}\n"
        f"📋 Tasks updated: "
        f"{counts['tasks']}\n"
        f"📄 Documents updated: "
        f"{counts['files']}\n"
        f"⚖️ Hearings updated: "
        f"{counts['hearings']}\n\n"
        f"Run /clienttimeline {case_value}"
    )


async def addtimeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/addtimeline CASE_NUMBER NOTE\n\n"
            "Example:\n"
            "/addtimeline CS/1635/2026 "
            "Client supplied identity proof"
        )
        return

    case_value = (
        context.args[0]
        .strip()
    )

    note = " ".join(
        context.args[1:]
    ).strip()

    try:
        event_id = (
            add_manual_timeline_event(
                case_value=case_value,
                event_title="Manual case note",
                event_details=note,
                created_by=(
                    update.effective_user.id
                )
            )
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Timeline note could not be added:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ TIMELINE NOTE ADDED\n\n"
        f"🆔 Event: {event_id}\n"
        f"🔢 Case: {case_value}\n"
        f"📝 {note}"
    )
