import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from services.ad_sync_v2 import (
    run_sync_v2,
)


async def synccasesv2(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "⏳ Running Advocate Diaries Sync v2...\n\n"
        "This sync imports client mobile numbers, "
        "updates client records and repairs linked cases."
    )

    try:
        result = await asyncio.to_thread(
            run_sync_v2
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Advocate Diaries Sync v2 failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ ADVOCATE DIARIES SYNC v2 COMPLETED\n\n"
        f"📥 Total cases fetched: "
        f"{result['total']}\n"
        f"➕ Cases added: "
        f"{result['added']}\n"
        f"🔄 Cases updated: "
        f"{result['updated']}\n"
        f"⏭ Cases skipped: "
        f"{result['skipped']}\n\n"
        f"👤 Clients created: "
        f"{result['client_created']}\n"
        f"👤 Clients updated: "
        f"{result['client_updated']}\n"
        f"📱 AD payloads containing mobile: "
        f"{result['payloads_with_mobile']}\n"
        f"📱 Mobile values added: "
        f"{result['mobile_added']}\n"
        f"✉️ Email values added: "
        f"{result['email_added']}\n"
        f"📍 Address values added: "
        f"{result['address_added']}\n\n"
        f"🛠 Existing cases repaired: "
        f"{result['cases_repaired']}\n"
        f"📁 Drive folders created/found: "
        f"{result['folders_created']}\n"
        f"♻️ Existing folders reused: "
        f"{result['folders_reused']}\n\n"
        "Next run:\n"
        "/generatehearingreminders"
    )


async def daily_ad_sync_v2_job(
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        result = await asyncio.to_thread(
            run_sync_v2
        )

        print(
            "DAILY AD SYNC v2 COMPLETED: "
            f"total={result['total']}, "
            f"added={result['added']}, "
            f"updated={result['updated']}, "
            f"mobiles_added="
            f"{result['mobile_added']}, "
            f"cases_repaired="
            f"{result['cases_repaired']}"
        )

    except Exception as exc:
        print(
            "DAILY AD SYNC v2 FAILED: "
            f"{type(exc).__name__}: {exc}"
        )
