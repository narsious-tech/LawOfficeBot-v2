"""Owner controls and scheduler hooks for WhatsApp case notifications."""
from __future__ import annotations

import asyncio
import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import CommandHandler, ContextTypes

from config import DATABASE_URL
from services.ad_sync_v3 import run_case_status_sync
from services.whatsapp_case_notifications import (
    case_notification_status,
    ensure_case_notification_schema,
    scan_case_notifications,
    set_phone_consent,
)


IST = ZoneInfo("Asia/Kolkata")


def _admin_private(update: Update) -> bool:
    raw = os.getenv("ADMIN_USER_ID", "").strip()
    return bool(
        raw.lstrip("-").isdigit()
        and update.effective_user
        and int(update.effective_user.id) == int(raw)
        and update.effective_chat
        and update.effective_chat.type == ChatType.PRIVATE
    )


async def _authorize(update: Update) -> bool:
    if _admin_private(update):
        return True
    await update.effective_message.reply_text(
        "🔒 WhatsApp case-notification controls are available only in Ajay's private chat."
    )
    return False


def _phone_for_case(case_ref: str) -> dict | None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(c.case_number),''),NULLIF(TRIM(c.case_id),'')) case_ref,
                    COALESCE(NULLIF(TRIM(c.case_title),''),'Case title not recorded') case_title,
                    COALESCE(
                        NULLIF(REGEXP_REPLACE(COALESCE(cc.whatsapp_number,''),'[^0-9]','','g'),''),
                        NULLIF(REGEXP_REPLACE(COALESCE(cl.whatsapp_number,''),'[^0-9]','','g'),''),
                        NULLIF(REGEXP_REPLACE(COALESCE(cl.mobile,''),'[^0-9]','','g'),''),
                        NULLIF(REGEXP_REPLACE(COALESCE(c.mobile,''),'[^0-9]','','g'),'')
                    ) phone
                FROM cases c
                LEFT JOIN clients cl ON c.client_id=cl.id OR (
                    c.ad_client_id IS NOT NULL AND c.ad_client_id=cl.ad_client_id
                )
                LEFT JOIN client_contacts cc ON cc.is_primary=TRUE AND LOWER(TRIM(cc.case_id)) IN (
                    LOWER(TRIM(COALESCE(c.case_id,''))),LOWER(TRIM(COALESCE(c.case_number,'')))
                )
                WHERE LOWER(TRIM(COALESCE(c.case_number,c.case_id,'')))=LOWER(TRIM(%s))
                ORDER BY c.id DESC,cc.id DESC NULLS LAST LIMIT 1
            """, (case_ref,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


async def whatsappreminderstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize(update):
        return
    status = await asyncio.to_thread(case_notification_status)
    await update.effective_message.reply_text(
        "📲 <b>WHATSAPP CASE NOTIFICATIONS</b>\n\n"
        f"D-2 reminders: <b>{'✅ Enabled' if status['reminders_enabled'] else '⚠️ Disabled'}</b>\n"
        f"Material updates: <b>{'✅ Enabled' if status['updates_enabled'] else '⚠️ Disabled'}</b>\n"
        f"AD hourly poll: <b>{'✅ Enabled' if status['poll_enabled'] else '⚠️ Disabled'}</b>\n"
        f"Transport: <b>{'✅ Ready' if status['transport_ready'] else '⚠️ Not ready'}</b>\n"
        f"Reminder lead: {status['reminder_days']} days\n\n"
        f"Opted in: {int(status.get('opted_in') or 0)}\n"
        f"Opted out: {int(status.get('opted_out') or 0)}\n"
        f"Sent this month: {int(status.get('sent_month') or 0)}\n"
        f"Estimated spend: ₹{float(status['month_spend']):.2f} / ₹{float(status['monthly_cap']):.2f}\n\n"
        f"Reminder template: <code>{html.escape(status['reminder_template'])}</code>\n"
        f"Update template: <code>{html.escape(status['update_template'])}</code>",
        parse_mode=ParseMode.HTML,
    )


async def testwhatsappreminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize(update):
        return
    await update.effective_message.reply_text("🔎 Running a dry scan. No WhatsApp message will be sent.")
    try:
        result = await asyncio.to_thread(scan_case_notifications, dry_run=True)
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Dry scan failed: {type(exc).__name__}: {exc}")
        return
    lines = [
        "🧪 <b>WHATSAPP REMINDER DRY RUN</b>", "",
        f"Cases checked: {result['cases']}",
        f"D-2 cases: {result['due_d2']}",
        f"Material changes: {result['material_changes']}",
        "", "Preview:",
    ]
    if not result["preview"]:
        lines.append("No reminder or material-change event is due.")
    for item in result["preview"]:
        lines.append(
            f"• {html.escape(item['event'])} · {html.escape(str(item['case'] or '-'))} "
            f"· phone …{html.escape(item['phone'])} · consent {html.escape(str(item['consent']))}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines)[:4000], parse_mode=ParseMode.HTML,
    )


async def whatsappconsent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize(update):
        return
    if len(context.args) != 2 or context.args[1].strip().lower() not in {"on", "off"}:
        await update.effective_message.reply_text(
            "Usage: /whatsappconsent CASE_NUMBER on|off\n"
            "Use ON only after the client has expressly requested WhatsApp reminders."
        )
        return
    case = await asyncio.to_thread(_phone_for_case, context.args[0])
    if not case:
        await update.effective_message.reply_text("❌ Case not found.")
        return
    if not case.get("phone"):
        await update.effective_message.reply_text("❌ No valid mobile number is linked to this case.")
        return
    opted_in = context.args[1].strip().lower() == "on"
    result = await asyncio.to_thread(
        set_phone_consent, case["phone"], opted_in, source="OWNER_RECORDED_CONSENT",
        updated_by=update.effective_user.id,
    )
    await update.effective_message.reply_text(
        f"✅ WhatsApp reminders {'enabled' if opted_in else 'disabled'}\n\n"
        f"⚖️ {case['case_ref']}\n"
        f"📱 +{result['phone_number'][:2]} ***** {result['phone_number'][-4:]}\n\n"
        "This setting applies to every linked case using the same client number."
    )


async def whatsapp_case_notification_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        result = await asyncio.to_thread(scan_case_notifications)
        print(
            "WhatsApp case notification scan: "
            f"cases={result['cases']} due_d2={result['due_d2']} "
            f"changes={result['material_changes']} sent={result['sent']} "
            f"failed={result['failed']} cap={result['cap_reached']}"
        )
        if result["cap_reached"]:
            today = datetime.now(IST).date().isoformat()
            if context.application.bot_data.get("wa_case_cap_alert") != today:
                admin_id = os.getenv("ADMIN_USER_ID", "").strip()
                if admin_id.lstrip("-").isdigit():
                    await context.bot.send_message(
                        int(admin_id),
                        "⚠️ WhatsApp case-notification monthly safety cap reached. "
                        "No further automatic paid templates were submitted.",
                    )
                context.application.bot_data["wa_case_cap_alert"] = today
    except Exception as exc:
        print(f"WhatsApp case notification scan failed: {type(exc).__name__}: {exc}")


async def advocate_diaries_status_poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if os.getenv("ADVOCATE_DIARIES_STATUS_POLL_ENABLED", "false").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return
    now = datetime.now(IST)
    if now.hour < 8 or now.hour >= 20:
        return
    try:
        stats = await asyncio.to_thread(run_case_status_sync)
        print(
            "AD lightweight status poll: "
            f"fetched={stats['cases_fetched']} updated={stats['cases_updated']} "
            f"unmatched={stats['cases_unmatched']}"
        )
        await whatsapp_case_notification_job(context)
    except Exception as exc:
        print(f"AD lightweight status poll failed: {type(exc).__name__}: {exc}")


def register_whatsapp_case_notification_handlers(app) -> None:
    ensure_case_notification_schema()
    app.add_handler(CommandHandler("whatsappreminderstatus", whatsappreminderstatus), group=-8)
    app.add_handler(CommandHandler("testwhatsappreminders", testwhatsappreminders), group=-8)
    app.add_handler(CommandHandler("whatsappconsent", whatsappconsent), group=-8)
