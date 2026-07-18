import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from services.ad_sync_v3 import (
    run_sync_v3,
)


async def synccasesv3(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "⏳ Running Advocate Diaries Sync v3...\n\n"
        "Cases and unique clients will be synchronized. "
        "Client phones, emails and addresses will then "
        "be copied into linked case records."
    )

    try:
        result = await asyncio.to_thread(
            run_sync_v3
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Advocate Diaries Sync v3 failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ ADVOCATE DIARIES SYNC v3 COMPLETED\n\n"

        f"⚖️ Cases fetched: "
        f"{result['cases_fetched']}\n"
        f"👥 Unique clients: "
        f"{result['unique_clients']}\n"
        f"✅ Client details fetched: "
        f"{result['clients_fetched']}\n"
        f"⚠️ Client fetch failures: "
        f"{result['client_fetch_failed']}\n\n"

        f"👤 Clients created: "
        f"{result['clients_created']}\n"
        f"🔄 Clients updated: "
        f"{result['clients_updated']}\n"
        f"📱 Mobiles imported: "
        f"{result['mobiles_imported']}\n"
        f"✉️ Emails imported: "
        f"{result['emails_imported']}\n"
        f"📍 Addresses imported: "
        f"{result['addresses_imported']}\n\n"

        f"➕ Cases added: "
        f"{result['cases_added']}\n"
        f"🔄 Cases updated: "
        f"{result['cases_updated']}\n"
        f"⏭ Cases skipped: "
        f"{result['cases_skipped']}\n"
        f"🛠 Existing cases repaired: "
        f"{result['cases_repaired']}\n\n"

        f"📁 Drive folders created/found: "
        f"{result['folders_created']}\n"
        f"♻️ Existing folders reused: "
        f"{result['folders_reused']}\n\n"

        "Next run:\n"
        "/generatehearingreminders"
    )


async def daily_ad_sync_v3_job(
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        result = await asyncio.to_thread(
            run_sync_v3
        )

        print(
            "DAILY AD SYNC v3 COMPLETED: "
            f"cases={result['cases_fetched']}, "
            f"clients={result['clients_fetched']}, "
            f"mobiles={result['mobiles_imported']}, "
            f"repaired={result['cases_repaired']}"
        )

    except Exception as exc:
        print(
            "DAILY AD SYNC v3 FAILED: "
            f"{type(exc).__name__}: {exc}"
        )
