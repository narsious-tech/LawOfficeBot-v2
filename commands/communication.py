import asyncio
import urllib.parse

import psycopg2

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import DATABASE_URL

from services.client_timeline import (
    log_communication_event,
    log_verification_event,
)
from services.whatsapp_cloud import send_logged_client_message, transport_ready

from services.communication_service import (
    get_db_connection,
    normalize_case_value,
    normalize_mobile,
    display_mobile,
    get_case_record,
    get_client_record,
    resolve_client_mobile,
    get_office_profile,
    make_communication_ref,
    client_is_verified,
    client_case_count,
    get_client_cases,
    build_welcome_message,
    build_new_case_message,
    build_case_status_message,
    save_mobile_for_case_and_client,
    create_message_log,
    update_message_status,
    mark_verification_sent,
    mark_case_verified,
    mark_change_requested,
    get_message_history,
)


WAITING_MOBILE = 7101


def message_type_label(
    message_type: str
) -> str:
    labels = {
        "CLIENT_WELCOME": "Client Welcome",
        "NEW_CASE": "New Case",
        "CASE_STATUS": "Case Status",
    }

    return labels.get(
        message_type,
        message_type
    )


def template_for_action(
    action: str,
    case,
    client,
    profile,
    communication_ref,
    existing_cases=None,
    resolved_mobile=None
):
    action = action.upper()

    if action == "WELCOME":
        return (
            "CLIENT_WELCOME",
            "client_welcome",
            build_welcome_message(
                case,
                profile,
                communication_ref,
                resolved_mobile=resolved_mobile
            )
        )

    if action == "NEW_CASE":
        return (
            "NEW_CASE",
            "returning_client_new_case",
            build_new_case_message(
                case,
                profile,
                communication_ref,
                existing_cases=existing_cases,
                resolved_mobile=resolved_mobile
            )
        )

    if action == "CASE_STATUS":
        return (
            "CASE_STATUS",
            "case_status",
            build_case_status_message(
                case,
                profile,
                communication_ref,
                resolved_mobile=resolved_mobile
            )
        )

    raise ValueError(
        "Unsupported communication action."
    )


async def prepare_client_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    case_value: str,
    action: str
):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return ConversationHandler.END

        client = get_client_record(
            cur,
            case
        )

        mobile = resolve_client_mobile(
            case,
            client
        )

        # Use the same resolved number in the WhatsApp URL,
        # preview header and client-facing message body.
        if mobile:
            case["mobile"] = mobile
            case["resolved_mobile"] = mobile

        if not mobile:
            context.user_data[
                "communication_pending"
            ] = {
                "case_value": case[
                    "canonical_case_id"
                ],
                "action": action,
            }

            await update.effective_message.reply_text(
                "⚠️ CLIENT MOBILE NUMBER MISSING\n\n"
                f"🔢 Case: "
                f"{case['canonical_case_id']}\n"
                f"👤 Client: "
                f"{case.get('client_name') or '-'}\n\n"
                "Please send the client's WhatsApp "
                "number now.\n\n"
                "Example:\n"
                "9876543210\n\n"
                "Use /cancelcommunication to cancel."
            )

            return WAITING_MOBILE

        profile = get_office_profile(
            cur
        )

        existing_cases = []

        if action.upper() == "NEW_CASE":
            existing_cases = get_client_cases(
                cur,
                case,
                mobile,
                exclude_current=True
            )

        communication_ref = (
            make_communication_ref()
        )

        (
            message_type,
            template_name,
            message_text
        ) = template_for_action(
            action,
            case,
            client,
            profile,
            communication_ref,
            existing_cases=existing_cases,
            resolved_mobile=(
                mobile
                or case.get("resolved_mobile")
                or case.get("mobile")
            )
        )

        message_id = create_message_log(
            cur,
            case=case,
            client=client,
            phone_number=mobile,
            message_type=message_type,
            message_text=message_text,
            sent_by=update.effective_user.id,
            communication_ref=(
                communication_ref
            ),
            template_name=template_name
        )

        log_communication_event(
            cur,
            case=case,
            message_id=message_id,
            message_type=message_type,
            delivery_status="DRAFT",
            communication_ref=communication_ref,
            created_by=update.effective_user.id
        )

        if message_type == "CLIENT_WELCOME":
            mark_verification_sent(
                cur,
                case
            )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Communication could not be prepared:\n"
            f"{type(exc).__name__}: {exc}"
        )

        return ConversationHandler.END

    finally:
        cur.close()
        conn.close()

    encoded = urllib.parse.quote(
        message_text,
        safe=""
    )

    whatsapp_url = (
        f"https://wa.me/{mobile}"
        f"?text={encoded}"
    )

    preview = (
        "📱 CLIENT COMMUNICATION PREVIEW\n\n"
        f"🆔 Message ID: {message_id}\n"
        f"🔖 Ref: {communication_ref}\n"
        f"📌 Type: "
        f"{message_type_label(message_type)}\n"
        f"🔢 Case: "
        f"{case['canonical_case_id']}\n"
        f"👤 Client: "
        f"{case.get('client_name') or '-'}\n"
        f"📱 Number: "
        f"{display_mobile(mobile)}\n\n"
        "MESSAGE\n\n"
        f"{message_text}\n\n"
        "Review the message before opening WhatsApp."
    )

    keyboard_rows = []
    if transport_ready():
        keyboard_rows.append([
            InlineKeyboardButton(
                "🚀 Send Automatically",
                callback_data=f"comm:api:{message_id}"
            )
        ])
    keyboard_rows.extend([
        [InlineKeyboardButton("📲 Open WhatsApp", url=whatsapp_url)],
        [
            InlineKeyboardButton(
                "✅ Mark Sent",
                callback_data=(
                    f"comm:sent:{message_id}"
                )
            ),
            InlineKeyboardButton(
                "❌ Cancel Draft",
                callback_data=(
                    f"comm:cancel:{message_id}"
                )
            )
        ],
    ])
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    await update.effective_message.reply_text(
        preview,
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

    return ConversationHandler.END


async def welcomeclient(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/welcomeclient CASE_NUMBER"
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        context.args[0]
    )

    return await prepare_client_message(
        update,
        context,
        case_value=case_value,
        action="WELCOME"
    )


async def sendnewcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/sendnewcase CASE_NUMBER"
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        context.args[0]
    )

    return await prepare_client_message(
        update,
        context,
        case_value=case_value,
        action="NEW_CASE"
    )


async def newcasewelcome(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Intelligent new-case communication.

    Verified client:
        Use the returning-client/new-case template.

    Unverified client:
        Use the full welcome and verification template.
    """
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/newcasewelcome CASE_NUMBER\n\n"
            "Example:\n"
            "/newcasewelcome CS/1635/2026"
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        context.args[0]
    )

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return ConversationHandler.END

        client = get_client_record(
            cur,
            case
        )

        verified = client_is_verified(
            case,
            client
        )

        case_count = client_case_count(
            cur,
            case,
            resolve_client_mobile(
                case,
                client
            )
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Client verification check failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return ConversationHandler.END

    finally:
        cur.close()
        conn.close()

    if verified:
        action = "NEW_CASE"

        await update.effective_message.reply_text(
            "✅ Existing verified client detected.\n\n"
            f"👤 Client: "
            f"{case.get('client_name') or '-'}\n"
            f"📂 Recorded Cases: {case_count}\n"
            "📨 Preparing the new-case information message..."
        )

    else:
        action = "WELCOME"

        await update.effective_message.reply_text(
            "ℹ️ Client details are not yet verified.\n\n"
            f"👤 Client: "
            f"{case.get('client_name') or '-'}\n"
            f"📂 Recorded Cases: {case_count}\n"
            "📨 Preparing the full welcome and "
            "verification message..."
        )

    return await prepare_client_message(
        update,
        context,
        case_value=case_value,
        action=action
    )


async def sendcasestatus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/sendcasestatus CASE_NUMBER"
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        context.args[0]
    )

    return await prepare_client_message(
        update,
        context,
        case_value=case_value,
        action="CASE_STATUS"
    )


async def receive_missing_mobile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    pending = context.user_data.get(
        "communication_pending"
    )

    if not pending:
        await update.effective_message.reply_text(
            "❌ No pending communication was found."
        )
        return ConversationHandler.END

    raw_mobile = (
        update.effective_message.text
        or ""
    ).strip()

    try:
        mobile = normalize_mobile(
            raw_mobile
        )

    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ {exc}\n\n"
            "Please send the mobile number again, "
            "or use /cancelcommunication."
        )
        return WAITING_MOBILE

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            pending["case_value"]
        )

        if not case:
            await update.effective_message.reply_text(
                "❌ Case not found."
            )
            return ConversationHandler.END

        save_mobile_for_case_and_client(
            cur,
            case,
            mobile
        )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Mobile number could not be saved:\n"
            f"{type(exc).__name__}: {exc}"
        )

        return ConversationHandler.END

    finally:
        cur.close()
        conn.close()

    context.user_data.pop(
        "communication_pending",
        None
    )

    await update.effective_message.reply_text(
        "✅ Client mobile number saved.\n\n"
        f"📱 {display_mobile(mobile)}\n\n"
        "Preparing the pending communication..."
    )

    return await prepare_client_message(
        update,
        context,
        case_value=pending["case_value"],
        action=pending["action"]
    )


async def cancelcommunication(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.pop(
        "communication_pending",
        None
    )

    await update.effective_message.reply_text(
        "❌ Communication cancelled."
    )

    return ConversationHandler.END


async def communication_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    parts = (
        query.data
        or ""
    ).split(":")

    if len(parts) != 3:
        await query.edit_message_text(
            "❌ Invalid communication action."
        )
        return

    _, action, message_id_text = parts

    if not message_id_text.isdigit():
        await query.edit_message_text(
            "❌ Invalid message ID."
        )
        return

    message_id = int(
        message_id_text
    )

    if action == "api":
        try:
            result = await asyncio.to_thread(
                send_logged_client_message, message_id
            )
            await query.edit_message_text(
                "✅ WHATSAPP MESSAGE SUBMITTED AUTOMATICALLY\n\n"
                f"🆔 Message ID: {message_id}\n"
                f"📡 Provider ID: {result['provider_message_id']}\n\n"
                "Delivery and read status will update through the webhook."
            )
        except Exception as exc:
            await query.edit_message_text(
                "❌ AUTOMATIC WHATSAPP SEND FAILED\n\n"
                f"{exc}\n\n"
                "The draft is retained as FAILED and can be retried after "
                "the configuration is corrected."
            )
        return

    status = {
        "sent": "SENT_MANUALLY",
        "cancel": "CANCELLED",
    }.get(action)

    if not status:
        await query.edit_message_text(
            "❌ Invalid communication action."
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        updated = update_message_status(
            cur,
            message_id=message_id,
            status=status
        )

        if updated:
            cur.execute("""
                SELECT
                    COALESCE(
                        NULLIF(TRIM(related_case_id), ''),
                        NULLIF(TRIM(case_id), '')
                    ),
                    message_type,
                    communication_ref
                FROM client_messages
                WHERE id = %s
                LIMIT 1
            """, (
                message_id,
            ))

            message_row = cur.fetchone()

            if message_row:
                (
                    timeline_case_value,
                    timeline_message_type,
                    timeline_ref
                ) = message_row

                timeline_case = get_case_record(
                    cur,
                    timeline_case_value
                )

                if timeline_case:
                    log_communication_event(
                        cur,
                        case=timeline_case,
                        message_id=message_id,
                        message_type=(
                            timeline_message_type
                            or "CLIENT_MESSAGE"
                        ),
                        delivery_status=status,
                        communication_ref=(
                            timeline_ref
                            or ""
                        ),
                        created_by=query.from_user.id
                    )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await query.edit_message_text(
            "❌ Communication log update failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not updated:
        await query.edit_message_text(
            "⚠️ Communication record was not found."
        )
        return

    if action == "sent":
        text = (
            "✅ WHATSAPP MESSAGE MARKED AS SENT\n\n"
            f"🆔 Message ID: {message_id}"
        )
    else:
        text = (
            "❌ COMMUNICATION DRAFT CANCELLED\n\n"
            f"🆔 Message ID: {message_id}"
        )

    await query.edit_message_text(
        text
    )


async def missingmobiles(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                COALESCE(
                    NULLIF(TRIM(case_number), ''),
                    NULLIF(TRIM(case_id), '')
                ) AS case_reference,
                client_name,
                case_title
            FROM cases
            WHERE
                TRIM(COALESCE(mobile, '')) = ''
            ORDER BY
                client_name ASC,
                case_reference ASC
            LIMIT 100
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "✅ All listed cases have a mobile number."
        )
        return

    message = (
        "📱 CASES WITHOUT CLIENT MOBILE\n\n"
        f"📌 Total shown: {len(rows)}\n\n"
    )

    for index, (
        case_reference,
        client_name,
        case_title
    ) in enumerate(
        rows,
        start=1
    ):
        message += (
            f"{index}. "
            f"{case_reference or '-'}\n"
            f"   👤 {client_name or '-'}\n"
        )

        if case_title:
            message += (
                f"   ⚖️ {case_title}\n"
            )

        message += (
            f"   /welcomeclient "
            f"{case_reference}\n\n"
        )

    await update.effective_message.reply_text(
        message[:3900]
    )


async def pendingclientverification(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                COALESCE(
                    NULLIF(TRIM(case_number), ''),
                    NULLIF(TRIM(case_id), '')
                ) AS case_reference,
                client_name,
                client_verification_status,
                client_verification_sent_at
            FROM cases
            WHERE COALESCE(
                client_verification_status,
                'NOT_SENT'
            ) NOT IN (
                'CONFIRMED',
                'VERIFIED',
                'DETAILS_CONFIRMED'
            )
            ORDER BY
                client_verification_sent_at
                ASC NULLS FIRST,
                client_name ASC
            LIMIT 100
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "✅ No pending client verifications."
        )
        return

    message = (
        "🧾 PENDING CLIENT VERIFICATIONS\n\n"
        f"📌 Total shown: {len(rows)}\n\n"
    )

    for index, (
        case_reference,
        client_name,
        status,
        sent_at
    ) in enumerate(
        rows,
        start=1
    ):
        message += (
            f"{index}. {case_reference or '-'}\n"
            f"   👤 {client_name or '-'}\n"
            f"   📌 Status: "
            f"{status or 'NOT_SENT'}\n"
            f"   🕒 Sent: "
            f"{sent_at or '-'}\n\n"
        )

    await update.effective_message.reply_text(
        message[:3900]
    )


async def confirmclientdetails(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/confirmclientdetails CASE_NUMBER"
        )
        return

    case_value = normalize_case_value(
        context.args[0]
    )

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return

        mark_case_verified(
            cur,
            case
        )

        log_verification_event(
            cur,
            case=case,
            status="CONFIRMED",
            created_by=update.effective_user.id
        )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Verification update failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    await update.effective_message.reply_text(
        "✅ CLIENT DETAILS CONFIRMED\n\n"
        f"🔢 Case: "
        f"{case['canonical_case_id']}\n"
        f"👤 Client: "
        f"{case.get('client_name') or '-'}"
    )


async def clientchanges(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/clientchanges CASE_NUMBER NOTE\n\n"
            "Example:\n"
            "/clientchanges CS/3528/2026 "
            "Mobile number needs correction"
        )
        return

    case_value = normalize_case_value(
        context.args[0]
    )

    note = " ".join(
        context.args[1:]
    ).strip()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case = get_case_record(
            cur,
            case_value
        )

        if not case:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return

        mark_change_requested(
            cur,
            case,
            note
        )

        log_verification_event(
            cur,
            case=case,
            status="CHANGE_REQUESTED",
            note=note,
            created_by=update.effective_user.id
        )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Client-change note could not be saved:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    await update.effective_message.reply_text(
        "🟠 CLIENT CHANGE REQUEST RECORDED\n\n"
        f"🔢 Case: "
        f"{case['canonical_case_id']}\n"
        f"👤 Client: "
        f"{case.get('client_name') or '-'}\n"
        f"📝 Note: {note}"
    )


async def messagehistory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/messagehistory CASE_NUMBER"
        )
        return

    case_value = normalize_case_value(
        context.args[0]
    )

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        rows = get_message_history(
            cur,
            case_value,
            limit=20
        )

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "No client communications were found "
            f"for {case_value}."
        )
        return

    message = (
        "📨 CLIENT COMMUNICATION HISTORY\n\n"
        f"🔢 Case: {case_value}\n"
        f"📌 Records: {len(rows)}\n\n"
    )

    for item in rows:
        message += (
            f"🆔 Message #{item.get('id', '-')}\n"
            f"📌 Type: "
            f"{item.get('message_type', '-')}\n"
            f"📊 Status: "
            f"{item.get('delivery_status', '-')}\n"
            f"🔖 Ref: "
            f"{item.get('communication_ref', '-')}\n"
            f"📱 Number: "
            f"{item.get('phone_number', '-')}\n"
            f"🕒 Created: "
            f"{item.get('created_at', '-')}\n"
            f"✅ Sent: "
            f"{item.get('sent_at', '-')}\n\n"
        )

    await update.effective_message.reply_text(
        message[:3900]
    )


def build_communication_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "welcomeclient",
                welcomeclient
            ),
            CommandHandler(
                "sendnewcase",
                sendnewcase
            ),
            CommandHandler(
                "newcasewelcome",
                newcasewelcome
            ),
            CommandHandler(
                "newcaseinfo",
                newcasewelcome
            ),
            CommandHandler(
                "sendcasestatus",
                sendcasestatus
            ),
        ],
        states={
            WAITING_MOBILE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    receive_missing_mobile
                )
            ]
        },
        fallbacks=[
            CommandHandler(
                "cancelcommunication",
                cancelcommunication
            )
        ],
        allow_reentry=True
    )
