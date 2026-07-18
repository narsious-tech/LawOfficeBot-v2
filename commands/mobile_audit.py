from telegram import Update
from telegram.ext import ContextTypes

from services.mobile_audit import (
    get_missing_mobile_report,
    repair_missing_mobiles,
    get_mobile_audit_summary,
)


def format_date(value):
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime(
            "%d-%m-%Y"
        )

    return str(value)


async def missingmobilesreport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        rows = get_missing_mobile_report(
            limit=200
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Missing-mobile report failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    if not rows:
        await update.effective_message.reply_text(
            "✅ MOBILE DATA HEALTHY\n\n"
            "All active cases have a usable client "
            "mobile number."
        )
        return

    message = (
        "📱 MISSING MOBILE REPORT\n\n"
        f"⚠️ Cases requiring attention: "
        f"{len(rows)}\n\n"
    )

    for index, item in enumerate(
        rows,
        start=1
    ):
        message += (
            f"{index}. "
            f"{item['case_reference']}\n"
            f"   👤 "
            f"{item['client_name']}\n"
            f"   📅 Next Hearing: "
            f"{format_date(item['next_hearing'])}\n"
        )

        if item.get("case_title"):
            message += (
                f"   ⚖️ "
                f"{item['case_title']}\n"
            )

        for reason in item["reasons"]:
            message += (
                f"   ❌ {reason}\n"
            )

        message += (
            f"   Add manually:\n"
            f"   /clientphone "
            f"{item['case_reference']} "
            f"9876543210\n\n"
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


async def repairmobiles(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "⏳ Repairing client links and missing "
        "case mobile numbers..."
    )

    try:
        result = repair_missing_mobiles()

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Mobile repair failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    await update.effective_message.reply_text(
        "✅ MOBILE REPAIR COMPLETED\n\n"
        f"🔎 Cases checked: "
        f"{result['cases_checked']}\n"
        f"🔗 Client links repaired: "
        f"{result['case_links_repaired']}\n"
        f"📱 Mobiles repaired: "
        f"{result['mobiles_repaired']}\n"
        f"⚠️ Still missing: "
        f"{result['still_missing']}\n\n"
        "Run /missingmobilesreport for details."
    )


async def mobileaudit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        result = get_mobile_audit_summary()

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Mobile audit failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    total = result["total_cases"]
    with_mobile = result[
        "cases_with_mobile"
    ]

    coverage = (
        round(
            with_mobile
            / total
            * 100,
            1
        )
        if total
        else 0
    )

    await update.effective_message.reply_text(
        "📊 CLIENT MOBILE DATA AUDIT\n\n"
        f"⚖️ Total cases: "
        f"{total}\n"
        f"✅ Cases with mobile: "
        f"{with_mobile}\n"
        f"⚠️ Cases without mobile: "
        f"{result['cases_without_mobile']}\n"
        f"🔗 Cases without local client link: "
        f"{result['cases_without_client_link']}\n"
        f"🌐 Cases without AD client link: "
        f"{result['cases_without_ad_client_link']}\n\n"
        f"📈 Mobile coverage: "
        f"{coverage}%\n\n"
        "Commands:\n"
        "/repairmobiles\n"
        "/missingmobilesreport"
    )
