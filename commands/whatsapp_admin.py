"""Administrator controls for Meta WhatsApp Cloud API."""
from __future__ import annotations

import asyncio
import html
import os

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CommandHandler, ContextTypes

from services.whatsapp_cloud import (
    ensure_whatsapp_schema,
    recent_inbound,
    retry_due_messages,
    send_text_message,
    send_logged_client_message,
    transport_ready,
    whatsapp_config,
)


def _admin(user_id: int | None) -> bool:
    raw = os.getenv("ADMIN_USER_ID", "").strip()
    if raw.lstrip("-").isdigit():
        return user_id is not None and int(user_id) == int(raw)
    return False


async def _authorize(update: Update) -> bool:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "🔒 WhatsApp administration is available only in Ajay’s private chat."
        )
        return False
    if not _admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ WhatsApp administration access denied.")
        return False
    return True


async def whatsappstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    cfg = whatsapp_config()
    await asyncio.to_thread(ensure_whatsapp_schema)
    public_url = (
        os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        or os.getenv("ATTENDANCE_APP_URL", "").strip()
    )
    if public_url and not public_url.startswith("http"):
        public_url = "https://" + public_url
    webhook = public_url.rstrip("/") + "/whatsapp/webhook" if public_url else "Not resolved"
    await update.effective_message.reply_text(
        "📲 <b>WHATSAPP CLOUD STATUS</b>\n\n"
        f"Transport: <b>{'✅ Ready' if transport_ready() else '⚠️ Not ready'}</b>\n"
        f"Enabled: {'Yes' if cfg['enabled'] else 'No'}\n"
        f"Phone Number ID: {'Configured' if cfg['phone_number_id'] else 'Missing'}\n"
        f"Access Token: {'Configured' if cfg['access_token'] else 'Missing'}\n"
        f"Verify Token: {'Configured' if cfg['verify_token'] else 'Missing'}\n"
        f"App Secret: {'Configured' if cfg['app_secret'] else 'Optional / missing'}\n"
        f"Graph API: {html.escape(cfg['graph_version'])}\n\n"
        f"Webhook URL:\n<code>{html.escape(webhook)}</code>\n\n"
        "Manual wa.me sending remains available as a fallback.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def testwhatsapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /testwhatsapp 919876543210\n"
            "This sends one real WhatsApp Cloud API test message."
        )
        return
    try:
        result = await asyncio.to_thread(
            send_text_message,
            context.args[0],
            "✅ Law Office WhatsApp Cloud API test successful.",
        )
        await update.effective_message.reply_text(
            "✅ WhatsApp test submitted.\n\n"
            f"Provider ID: {result['provider_message_id']}\n"
            "Use /whatsappstatus and Meta webhook delivery updates to confirm delivery."
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ WhatsApp test failed:\n{exc}")


async def whatsappinbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    rows = await asyncio.to_thread(recent_inbound, 15)
    lines = ["📨 <b>RECENT CLIENT WHATSAPP MESSAGES</b>", ""]
    if not rows:
        lines.append("No inbound WhatsApp messages have been received.")
    for item in rows:
        lines.extend([
            f"👤 <b>{html.escape(str(item.get('sender_name') or 'Unknown'))}</b>",
            f"📱 +{html.escape(str(item.get('sender_phone') or '-'))}",
            f"🔢 Case: {html.escape(str(item.get('related_case_id') or 'Not matched'))}",
            f"💬 {html.escape(str(item.get('message_text') or '-'))}",
            f"🕒 {html.escape(str(item.get('received_at') or '-'))}",
            "──────────",
        ])
    await update.effective_message.reply_text(
        "\n".join(lines)[:4000], parse_mode=ParseMode.HTML
    )


async def retrywhatsapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorize(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /retrywhatsapp MESSAGE_ID")
        return
    try:
        result = await asyncio.to_thread(
            send_logged_client_message, int(context.args[0])
        )
        await update.effective_message.reply_text(
            "✅ WhatsApp message resubmitted.\n"
            f"Provider ID: {result['provider_message_id']}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Retry failed:\n{exc}")


def register_whatsapp_handlers(app) -> None:
    ensure_whatsapp_schema()
    app.add_handler(CommandHandler("whatsappstatus", whatsappstatus), group=-8)
    app.add_handler(CommandHandler("testwhatsapp", testwhatsapp), group=-8)
    app.add_handler(CommandHandler("whatsappinbox", whatsappinbox), group=-8)
    app.add_handler(CommandHandler("retrywhatsapp", retrywhatsapp), group=-8)


async def whatsapp_retry_job(context: ContextTypes.DEFAULT_TYPE):
    if not transport_ready():
        return
    await asyncio.to_thread(retry_due_messages, 20)
