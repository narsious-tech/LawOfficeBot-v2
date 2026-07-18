import json

from telegram import Update
from telegram.ext import ContextTypes

from services.ad_api_diagnostics import (
    fetch_case_by_search,
    extract_possible_client_ids,
    try_client_endpoints,
    pretty_json,
)


async def inspectadcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/inspectadcase CASE_NUMBER\n\n"
            "Example:\n"
            "/inspectadcase COMA/74737/2026"
        )
        return

    case_value = (
        context.args[0]
        .strip()
    )

    await update.effective_message.reply_text(
        "⏳ Inspecting raw Advocate Diaries "
        "case payload..."
    )

    try:
        result = fetch_case_by_search(
            case_value
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Advocate Diaries case inspection failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    if not result.get("success"):
        await update.effective_message.reply_text(
            "❌ No Advocate Diaries case payload "
            "could be retrieved.\n\n"
            f"Status: "
            f"{result.get('status_code')}\n"
            f"Response:\n"
            f"{result.get('response_text')}"
        )
        return

    payload = result["payload"]

    client_ids = (
        extract_possible_client_ids(
            payload
        )
    )

    summary = (
        "✅ ADVOCATE DIARIES CASE INSPECTION\n\n"
        f"🔢 Requested case: {case_value}\n"
        f"🌐 Endpoint: "
        f"{result.get('endpoint')}\n"
        f"🔎 Parameters: "
        f"{result.get('params')}\n"
        f"👤 Possible client IDs: "
        f"{', '.join(client_ids) if client_ids else 'None found'}\n"
    )

    if result.get("warning"):
        summary += (
            f"\n⚠️ {result['warning']}\n"
        )

    await update.effective_message.reply_text(
        summary
    )

    raw_text = pretty_json(
        payload,
        limit=12000
    )

    while raw_text:
        chunk = raw_text[:3800]
        raw_text = raw_text[3800:]

        await update.effective_message.reply_text(
            "```json\n"
            f"{chunk}\n"
            "```",
            parse_mode="Markdown"
        )


async def inspectadclient(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/inspectadclient CLIENT_ID\n\n"
            "First run:\n"
            "/inspectadcase CASE_NUMBER\n\n"
            "Then use one of the possible client IDs "
            "shown by that command."
        )
        return

    client_id = (
        context.args[0]
        .strip()
    )

    await update.effective_message.reply_text(
        "⏳ Testing possible Advocate Diaries "
        "client endpoints..."
    )

    try:
        results = try_client_endpoints(
            client_id
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Advocate Diaries client inspection failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    header = (
        "🧪 ADVOCATE DIARIES CLIENT ENDPOINT TEST\n\n"
        f"👤 Client ID: {client_id}\n"
        f"🔬 Endpoints tested: {len(results)}\n\n"
    )

    await update.effective_message.reply_text(
        header
    )

    for index, item in enumerate(
        results,
        start=1
    ):
        status = item.get(
            "status_code",
            "ERROR"
        )

        body = pretty_json(
            item.get(
                "body",
                item.get("error")
            ),
            limit=5000
        )

        message = (
            f"TEST {index}\n"
            f"URL: {item.get('url')}\n"
            f"Params: {item.get('params')}\n"
            f"Status: {status}\n\n"
            f"{body}"
        )

        while message:
            chunk = message[:3800]
            message = message[3800:]

            await update.effective_message.reply_text(
                chunk,
                disable_web_page_preview=True
            )
