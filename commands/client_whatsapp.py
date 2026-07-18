import re
import urllib.parse
from datetime import datetime

import psycopg2
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from config import DATABASE_URL


INDIA_COUNTRY_CODE = "91"


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def normalize_case_value(value: str) -> str:
    return (value or "").strip()


def normalize_whatsapp_number(value: str) -> str:
    """
    Return digits only in international format.

    Examples:
        9876543210     -> 919876543210
        +919876543210  -> 919876543210
        00919876543210 -> 919876543210
    """
    digits = re.sub(r"\D", "", value or "")

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = INDIA_COUNTRY_CODE + digits

    if not digits.startswith(INDIA_COUNTRY_CODE):
        raise ValueError(
            "Use a valid Indian WhatsApp number, "
            "for example 9876543210 or +919876543210."
        )

    if len(digits) != 12:
        raise ValueError(
            "WhatsApp number must contain 10 Indian digits "
            "or 12 digits including country code 91."
        )

    return digits


def format_date(value):
    if not value:
        return "-"

    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")

    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y")

    text = str(value).strip()

    for pattern in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(
                text,
                pattern
            ).strftime("%d-%m-%Y")

        except ValueError:
            pass

    return text


def get_case_record(cur, case_value: str):
    """
    Find a case by case_id or case_number using the current cases schema.
    """
    cur.execute("""
        SELECT
            id,
            COALESCE(
                NULLIF(TRIM(case_id), ''),
                NULLIF(TRIM(case_number), '')
            ) AS canonical_case_id,
            case_number,
            case_title,
            client_name,
            COALESCE(
                next_hearing,
                hearing_date
            ) AS next_hearing_date,
            court_name,
            judge_name,
            status,
            mobile
        FROM cases
        WHERE
            LOWER(TRIM(COALESCE(case_id, '')))
                = LOWER(TRIM(%s))
            OR
            LOWER(TRIM(COALESCE(case_number, '')))
                = LOWER(TRIM(%s))
        ORDER BY id DESC
        LIMIT 1
    """, (
        case_value,
        case_value
    ))

    return cur.fetchone()


def get_primary_contact(cur, case_id: str):
    cur.execute("""
        SELECT
            id,
            client_name,
            whatsapp_number,
            consent_status
        FROM client_contacts
        WHERE LOWER(TRIM(case_id))
              = LOWER(TRIM(%s))
          AND is_primary = TRUE
        ORDER BY id DESC
        LIMIT 1
    """, (
        case_id,
    ))

    return cur.fetchone()


def upsert_primary_contact(
    cur,
    *,
    case_id,
    client_name,
    whatsapp_number,
    consent_status="UNKNOWN"
):
    cur.execute("""
        INSERT INTO client_contacts
        (
            case_id,
            client_name,
            whatsapp_number,
            consent_status,
            is_primary,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s,
            %s,
            TRUE,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (
            LOWER(TRIM(case_id))
        )
        WHERE is_primary = TRUE
        DO UPDATE SET
            client_name =
                EXCLUDED.client_name,
            whatsapp_number =
                EXCLUDED.whatsapp_number,
            consent_status =
                COALESCE(
                    client_contacts.consent_status,
                    EXCLUDED.consent_status
                ),
            updated_at =
                CURRENT_TIMESTAMP
        RETURNING
            id,
            client_name,
            whatsapp_number,
            consent_status
    """, (
        case_id,
        client_name,
        whatsapp_number,
        consent_status
    ))

    return cur.fetchone()


def build_case_status_message(
    *,
    client_name,
    case_title,
    case_number,
    next_hearing,
    court_name,
    judge_name,
    status
):
    safe_client_name = (
        client_name
        or "Client"
    )

    safe_case_title = (
        case_title
        or "-"
    )

    safe_case_number = (
        case_number
        or "-"
    )

    safe_next_hearing = (
        format_date(next_hearing)
        if next_hearing
        else "Not presently available"
    )

    safe_court = (
        court_name
        or "Not presently available"
    )

    safe_judge = (
        judge_name
        or "Not presently available"
    )

    safe_status = (
        status
        or "Matter is under office review."
    )

    return (
        f"Dear {safe_client_name},\n\n"
        "CASE STATUS UPDATE\n\n"
        f"Case: {safe_case_title}\n"
        f"Case No.: {safe_case_number}\n"
        f"Next Hearing: {safe_next_hearing}\n"
        f"Court: {safe_court}\n"
        f"Judge: {safe_judge}\n"
        f"Current Status: {safe_status}\n\n"
        "Regards\n"
        "Law Office of Ajay Chawla"
    )


async def clientphone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/clientphone CASE_NUMBER PHONE\n\n"
            "Examples:\n"
            "/clientphone CS/3528/2026 9876543210\n"
            "/clientphone CS/3528/2026 +919876543210"
        )
        return

    case_value = normalize_case_value(
        context.args[0]
    )

    phone_value = " ".join(
        context.args[1:]
    ).strip()

    try:
        whatsapp_number = normalize_whatsapp_number(
            phone_value
        )

    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ {exc}"
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case_row = get_case_record(
            cur,
            case_value
        )

        if not case_row:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return

        (
            _case_db_id,
            canonical_case_id,
            case_number,
            case_title,
            client_name,
            next_hearing,
            court_name,
            judge_name,
            status,
            existing_mobile
        ) = case_row

        canonical_case_id = (
            canonical_case_id
            or case_number
            or case_value
        )

        upsert_primary_contact(
            cur,
            case_id=canonical_case_id,
            client_name=client_name,
            whatsapp_number=whatsapp_number
        )

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Client WhatsApp number could not be saved:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    await update.effective_message.reply_text(
        "✅ CLIENT WHATSAPP SAVED\n\n"
        f"🔢 Case: {canonical_case_id}\n"
        f"⚖️ {case_title or '-'}\n"
        f"👤 Client: {client_name or '-'}\n"
        f"📱 WhatsApp: +{whatsapp_number}\n\n"
        f"Use /sendcasestatus {canonical_case_id}"
    )


async def sendcasestatus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/sendcasestatus CASE_NUMBER\n\n"
            "Example:\n"
            "/sendcasestatus CS/3528/2026"
        )
        return

    case_value = normalize_case_value(
        context.args[0]
    )

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        case_row = get_case_record(
            cur,
            case_value
        )

        if not case_row:
            await update.effective_message.reply_text(
                f"❌ Case not found: {case_value}"
            )
            return

        (
            _case_db_id,
            canonical_case_id,
            case_number,
            case_title,
            case_client_name,
            next_hearing,
            court_name,
            judge_name,
            status,
            existing_mobile
        ) = case_row

        canonical_case_id = (
            canonical_case_id
            or case_number
            or case_value
        )

        contact = get_primary_contact(
            cur,
            canonical_case_id
        )

        if not contact and existing_mobile:
            try:
                normalized_mobile = normalize_whatsapp_number(
                    str(existing_mobile)
                )

                contact = upsert_primary_contact(
                    cur,
                    case_id=canonical_case_id,
                    client_name=case_client_name,
                    whatsapp_number=normalized_mobile
                )

                conn.commit()

            except ValueError:
                contact = None

        if not contact:
            await update.effective_message.reply_text(
                "❌ No valid WhatsApp number is available "
                "for this case.\n\n"
                "Save it using:\n"
                f"/clientphone {canonical_case_id} 9876543210"
            )
            return

        (
            contact_id,
            contact_client_name,
            whatsapp_number,
            consent_status
        ) = contact

        client_name = (
            contact_client_name
            or case_client_name
            or "Client"
        )

        message_text = build_case_status_message(
            client_name=client_name,
            case_title=case_title,
            case_number=(
                case_number
                or canonical_case_id
            ),
            next_hearing=next_hearing,
            court_name=court_name,
            judge_name=judge_name,
            status=status
        )

        cur.execute("""
            INSERT INTO client_messages
            (
                case_id,
                client_name,
                phone_number,
                channel,
                message_type,
                message_text,
                sent_by,
                delivery_status,
                created_at
            )
            VALUES (
                %s, %s, %s,
                'WHATSAPP',
                'CASE_STATUS',
                %s, %s,
                'DRAFT',
                CURRENT_TIMESTAMP
            )
            RETURNING id
        """, (
            canonical_case_id,
            client_name,
            whatsapp_number,
            message_text,
            update.effective_user.id
        ))

        message_id = cur.fetchone()[0]
        conn.commit()

    except Exception as exc:
        conn.rollback()

        await update.effective_message.reply_text(
            "❌ Case-status message could not be prepared:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    encoded_message = urllib.parse.quote(
        message_text,
        safe=""
    )

    whatsapp_url = (
        f"https://wa.me/{whatsapp_number}"
        f"?text={encoded_message}"
    )

    context.user_data[
        "pending_client_message_id"
    ] = message_id

    preview = (
        "📱 WHATSAPP CASE-STATUS PREVIEW\n\n"
        f"🆔 Draft ID: {message_id}\n"
        f"🔢 Case: {canonical_case_id}\n"
        f"👤 Client: {client_name}\n"
        f"📱 Number: +{whatsapp_number}\n"
        f"📌 Consent: {consent_status}\n\n"
        "MESSAGE\n\n"
        f"{message_text}\n\n"
        "Review the message before opening WhatsApp."
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
                "✅ Mark Sent",
                callback_data=(
                    f"wa_status:sent:{message_id}"
                )
            ),
            InlineKeyboardButton(
                "❌ Cancel Draft",
                callback_data=(
                    f"wa_status:cancel:{message_id}"
                )
            )
        ]
    ])

    await update.effective_message.reply_text(
        preview,
        reply_markup=keyboard,
        disable_web_page_preview=True
    )


async def whatsapp_status_callback(
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
            "❌ Invalid WhatsApp action."
        )
        return

    _, action, message_id_text = parts

    if not message_id_text.isdigit():
        await query.edit_message_text(
            "❌ Invalid WhatsApp draft ID."
        )
        return

    message_id = int(
        message_id_text
    )

    if action not in [
        "sent",
        "cancel"
    ]:
        await query.edit_message_text(
            "❌ Invalid WhatsApp action."
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if action == "sent":
            cur.execute("""
                UPDATE client_messages
                SET
                    delivery_status = 'SENT_MANUALLY',
                    sent_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND delivery_status = 'DRAFT'
                RETURNING
                    case_id,
                    client_name,
                    phone_number
            """, (
                message_id,
            ))

        else:
            cur.execute("""
                UPDATE client_messages
                SET
                    delivery_status = 'CANCELLED'
                WHERE id = %s
                  AND delivery_status = 'DRAFT'
                RETURNING
                    case_id,
                    client_name,
                    phone_number
            """, (
                message_id,
            ))

        updated = cur.fetchone()

        if not updated:
            conn.rollback()

            await query.edit_message_text(
                "⚠️ This WhatsApp draft was already processed "
                "or could not be found."
            )
            return

        conn.commit()

    except Exception as exc:
        conn.rollback()

        await query.edit_message_text(
            "❌ WhatsApp draft update failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    (
        case_id,
        client_name,
        phone_number
    ) = updated

    if action == "sent":
        text = (
            "✅ WHATSAPP MESSAGE MARKED AS SENT\n\n"
            f"🆔 Draft ID: {message_id}\n"
            f"🔢 Case: {case_id}\n"
            f"👤 Client: {client_name or '-'}\n"
            f"📱 Number: +{phone_number}"
        )

    else:
        text = (
            "❌ WHATSAPP DRAFT CANCELLED\n\n"
            f"🆔 Draft ID: {message_id}\n"
            f"🔢 Case: {case_id}\n"
            f"👤 Client: {client_name or '-'}"
        )

    await query.edit_message_text(
        text
    )
