"""Telegram interface and scheduler job for office email alerts."""
from __future__ import annotations

import asyncio
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from services.email_alert_service import (
    acknowledge,
    alert_recipient_ids,
    email_alerts_enabled,
    mailbox_configs,
    monitor_status,
    scan_all_mailboxes,
)

logger = logging.getLogger(__name__)


def _admin_ids() -> set[int]:
    raw = ",".join(filter(None, [
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
        os.getenv("AI_ADMIN_USER_IDS", ""),
    ]))
    result: set[int] = set()
    for item in raw.split(","):
        try:
            result.add(int(item.strip()))
        except (TypeError, ValueError):
            pass
    return result


def _is_admin(user_id: int | None) -> bool:
    admins = _admin_ids()
    return bool(user_id and (not admins or int(user_id) in admins))


def _keyboard(mailbox_key: str, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Email Checked",
                callback_data=f"emack:{mailbox_key}:{uid}:checked",
            ),
            InlineKeyboardButton(
                "📌 Action Required",
                callback_data=f"emack:{mailbox_key}:{uid}:action",
            ),
        ]
    ])


async def email_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not email_alerts_enabled():
        return

    alerts, errors = await asyncio.to_thread(scan_all_mailboxes)
    for error in errors:
        logger.error("Office email monitor: %s", error)

    recipients = await asyncio.to_thread(alert_recipient_ids)
    if alerts and not recipients:
        logger.error("Office email alerts found messages but no linked Preet/Priya recipients.")
        return

    for alert in alerts:
        text = (
            f"📨 NEW OFFICE EMAIL — {alert.mailbox_label}\n\n"
            f"From: {alert.sender}\n"
            f"Subject: {alert.subject}\n"
            f"Received: {alert.received}\n\n"
            "Preet and Priya: please open the office mailbox, review this email, "
            "and confirm below."
        )
        for telegram_id in recipients:
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    reply_markup=_keyboard(alert.mailbox_key, alert.uid),
                )
            except Exception:
                logger.exception(
                    "Email alert delivery failed for Telegram user %s", telegram_id
                )


async def email_alert_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, mailbox_key, uid_text, status = query.data.split(":", 3)
        uid = int(uid_text)
        if status not in {"checked", "action"}:
            raise ValueError("Invalid acknowledgement status")
    except (ValueError, AttributeError):
        await query.answer("Invalid email acknowledgement.", show_alert=True)
        return

    user = update.effective_user
    await asyncio.to_thread(
        acknowledge, mailbox_key, uid, int(user.id), status
    )
    staff_name = user.full_name or str(user.id)
    confirmation = (
        f"✅ Confirmed checked by {staff_name}."
        if status == "checked"
        else f"📌 Action required — flagged by {staff_name}."
    )
    original = query.message.text or "Office email alert"
    if confirmation not in original:
        await query.edit_message_text(
            f"{original}\n\n{confirmation}",
            reply_markup=None,
        )

    if status == "action":
        for admin_id in _admin_ids():
            try:
                await context.bot.send_message(
                    admin_id,
                    f"📌 OFFICE EMAIL REQUIRES ACTION\n\n"
                    f"Flagged by: {staff_name}\n"
                    f"Mailbox: {mailbox_key.title()}\n\n"
                    f"{original[:2500]}",
                )
            except Exception:
                logger.exception("Could not escalate email alert to %s", admin_id)


async def email_alert_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ This status is restricted to Ajay.")
        return

    rows = await asyncio.to_thread(monitor_status)
    recipients = await asyncio.to_thread(alert_recipient_ids)
    lines = [
        "📨 OFFICE EMAIL ALERT STATUS",
        "",
        f"Monitor: {'✅ Enabled' if email_alerts_enabled() else '⏸ Disabled'}",
        f"Recipients linked: {len(recipients)}",
    ]
    for row in rows:
        icon = "✅" if row["configured"] and not row["last_error"] else (
            "⚠️" if row["configured"] else "❌"
        )
        lines.extend([
            "",
            f"{icon} {row['label']}",
            f"Configured: {'Yes' if row['configured'] else 'No'}",
            f"Baseline ready: {'Yes' if row['initialized'] else 'No'}",
            f"Last successful check: {row['last_success_at'] or 'Not yet'}",
        ])
        if row["last_error"]:
            lines.append(f"Last error: {str(row['last_error'])[:300]}")
    await update.effective_message.reply_text("\n".join(lines))


async def test_email_alerts(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ This test is restricted to Ajay.")
        return
    await update.effective_message.reply_text(
        "🔄 Checking the configured Gmail and Yahoo inboxes now…"
    )
    await email_monitor_job(context)
    await email_alert_status(update, context)