import urllib.parse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from services.hearing_automation import (
    ensure_hearing_automation_tables,
    generate_hearing_reminder_queue,
    get_pending_hearing_reminders,
    get_hearing_reminder,
    approve_hearing_reminder,
    cancel_hearing_reminder,
    format_hearing_date_long,
)


async def generatehearingreminders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        counts = (
            generate_hearing_reminder_queue()
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Hearing reminder generation failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ HEARING REMINDER QUEUE UPDATED\n\n"
        f"⚖️ Cases checked: "
        f"{counts['cases_checked']}\n"
        f"➕ New reminders: "
        f"{counts['created']}\n"
        f"♻️ Existing reminders: "
        f"{counts['existing']}\n"
        f"⚠️ Missing mobile: "
        f"{counts['missing_mobile']}\n\n"
        "Run /hearingqueue to review."
    )


async def hearingqueue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        rows = get_pending_hearing_reminders(
            limit=100
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Hearing queue could not be loaded:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    if not rows:
        await update.effective_message.reply_text(
            "✅ No pending hearing reminders."
        )
        return

    message = (
        "📨 HEARING REMINDER QUEUE\n\n"
        f"📌 Pending: {len(rows)}\n\n"
    )

    for item in rows:
        message += (
            f"🆔 Reminder #{item['id']}\n"
            f"🔢 {item['case_number']}\n"
            f"👤 {item['client_name'] or '-'}\n"
            f"📅 "
            f"{format_hearing_date_long(item['hearing_date'])}\n"
            f"⏳ {item['reminder_type']}\n"
            f"📱 {item['phone_number'] or '-'}\n"
            f"/hearingpreview {item['id']}\n"
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
            message = (
                message[split_at:]
                .lstrip()
            )

        await update.effective_message.reply_text(
            chunk
        )


async def hearingpreview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/hearingpreview REMINDER_ID"
        )
        return

    reminder_id_text = (
        context.args[0]
        .strip()
    )

    if not reminder_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ REMINDER_ID must be a number."
        )
        return

    reminder = get_hearing_reminder(
        int(reminder_id_text)
    )

    if not reminder:
        await update.effective_message.reply_text(
            "❌ Hearing reminder not found."
        )
        return

    encoded = urllib.parse.quote(
        reminder["message_text"],
        safe=""
    )

    whatsapp_url = (
        f"https://wa.me/"
        f"{reminder['phone_number']}"
        f"?text={encoded}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=(
                    f"hear:approve:"
                    f"{reminder['id']}"
                )
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=(
                    f"hear:cancel:"
                    f"{reminder['id']}"
                )
            )
        ]
    ])

    if (
        reminder["queue_status"]
        == "APPROVED"
    ):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📲 Open WhatsApp",
                    url=whatsapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=(
                        f"hear:cancel:"
                        f"{reminder['id']}"
                    )
                )
            ]
        ])

    await update.effective_message.reply_text(
        "📱 HEARING REMINDER PREVIEW\n\n"
        f"🆔 Reminder: "
        f"#{reminder['id']}\n"
        f"🔢 Case: "
        f"{reminder['case_number']}\n"
        f"👤 Client: "
        f"{reminder['client_name'] or '-'}\n"
        f"📅 Hearing: "
        f"{format_hearing_date_long(reminder['hearing_date'])}\n"
        f"📌 Status: "
        f"{reminder['queue_status']}\n\n"
        "MESSAGE\n\n"
        f"{reminder['message_text']}",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )


async def hearing_automation_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    parts = (
        query.data
        or ""
    ).split(":")

    if (
        len(parts) != 3
        or not parts[2].isdigit()
    ):
        await query.edit_message_text(
            "❌ Invalid hearing reminder action."
        )
        return

    action = parts[1]
    reminder_id = int(
        parts[2]
    )

    try:
        if action == "approve":
            reminder = approve_hearing_reminder(
                reminder_id=reminder_id,
                approved_by=query.from_user.id
            )

            if not reminder:
                await query.edit_message_text(
                    "❌ Hearing reminder not found."
                )
                return

            encoded = urllib.parse.quote(
                reminder["message_text"],
                safe=""
            )

            whatsapp_url = (
                f"https://wa.me/"
                f"{reminder['phone_number']}"
                f"?text={encoded}"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📲 Open WhatsApp",
                        url=whatsapp_url
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=(
                            f"hear:cancel:"
                            f"{reminder_id}"
                        )
                    )
                ]
            ])

            await query.edit_message_text(
                "✅ HEARING REMINDER APPROVED\n\n"
                f"🆔 Reminder: #{reminder_id}\n"
                f"🔢 Case: "
                f"{reminder['case_number']}\n"
                f"👤 Client: "
                f"{reminder['client_name'] or '-'}\n\n"
                "Use the button below to open WhatsApp.",
                reply_markup=keyboard
            )

        elif action == "cancel":
            cancelled = cancel_hearing_reminder(
                reminder_id=reminder_id,
                cancelled_by=query.from_user.id
            )

            if not cancelled:
                await query.edit_message_text(
                    "⚠️ Reminder was not pending or approved."
                )
                return

            await query.edit_message_text(
                "❌ HEARING REMINDER CANCELLED\n\n"
                f"🆔 Reminder: #{reminder_id}"
            )

        else:
            await query.edit_message_text(
                "❌ Unsupported hearing reminder action."
            )

    except Exception as exc:
        await query.edit_message_text(
            "❌ Hearing reminder action failed:\n"
            f"{type(exc).__name__}: {exc}"
        )


async def hearing_reminder_generation_job(
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Scheduled job: generate queue entries only.
    It does not send anything automatically.
    """
    try:
        counts = (
            generate_hearing_reminder_queue()
        )

        print(
            "HEARING REMINDER JOB: "
            f"created={counts['created']}, "
            f"existing={counts['existing']}, "
            f"missing_mobile="
            f"{counts['missing_mobile']}"
        )

    except Exception as exc:
        print(
            "HEARING REMINDER JOB FAILED: "
            f"{type(exc).__name__}: {exc}"
        )
