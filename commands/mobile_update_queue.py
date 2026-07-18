from telegram import Update
from telegram.ext import ContextTypes

from services.mobile_update_queue import (
    get_mobile_update_queue,
    get_mobile_update_queue_summary,
)


def _format_date(value):
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime(
            "%d-%m-%Y"
        )

    return str(value)


async def mobileupdatequeue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        queue = get_mobile_update_queue(
            limit=300
        )
        summary = (
            get_mobile_update_queue_summary()
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Mobile update queue failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    if not queue:
        await update.effective_message.reply_text(
            "✅ MOBILE UPDATE QUEUE CLEAR\n\n"
            "Every active client has a usable "
            "mobile number."
        )
        return

    message = (
        "📱 MOBILE UPDATE QUEUE\n\n"
        f"👥 Clients requiring update: "
        f"{summary['clients_pending']}\n"
        f"⚖️ Active cases affected: "
        f"{summary['affected_cases']}\n"
        f"🌐 Clients linked with AD: "
        f"{summary['clients_with_ad_id']}\n"
        f"⚠️ Clients without AD link: "
        f"{summary['clients_without_ad_id']}\n\n"
        "Update each client once in Advocate Diaries. "
        "All linked cases will be repaired after "
        "/synccasesv3.\n\n"
    )

    for index, item in enumerate(
        queue,
        start=1
    ):
        message += (
            f"{index}. 👤 "
            f"{item['client_name']}\n"
            f"   📂 Active matters: "
            f"{item['active_case_count']}\n"
            f"   🌐 AD Client ID: "
            f"{item.get('ad_client_id') or '-'}\n"
            f"   ❌ {item['reason']}\n"
        )

        if item.get("email"):
            message += (
                f"   ✉️ "
                f"{item['email']}\n"
            )

        if item.get("address"):
            short_address = (
                item["address"][:120]
            )

            if len(item["address"]) > 120:
                short_address += "..."

            message += (
                f"   📍 "
                f"{short_address}\n"
            )

        message += (
            "   ⚖️ Linked cases:\n"
        )

        cases = item["cases"]

        for case_index, case in enumerate(
            cases[:5],
            start=1
        ):
            message += (
                f"      {case_index}. "
                f"{case['case_reference']}"
                f" | "
                f"{_format_date(case['next_hearing'])}\n"
            )

        remaining = len(cases) - 5

        if remaining > 0:
            message += (
                f"      ...and "
                f"{remaining} more case(s)\n"
            )

        primary_case = cases[0][
            "case_reference"
        ]

        message += (
            "   After AD update:\n"
            "   /synccasesv3\n"
            f"   Verify: /newcasewelcome "
            f"{primary_case}\n\n"
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
            chunk,
            disable_web_page_preview=True
        )


async def mobileupdatequeuesummary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        summary = (
            get_mobile_update_queue_summary()
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Mobile queue summary failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "📊 MOBILE UPDATE WORKLOAD\n\n"
        f"👥 Clients pending: "
        f"{summary['clients_pending']}\n"
        f"⚖️ Cases affected: "
        f"{summary['affected_cases']}\n"
        f"🌐 With AD client ID: "
        f"{summary['clients_with_ad_id']}\n"
        f"⚠️ Without AD client ID: "
        f"{summary['clients_without_ad_id']}\n\n"
        "Run /mobileupdatequeue for the "
        "client-wise checklist."
    )
