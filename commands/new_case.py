"""
New-case conversation for LawOfficeBot-v2.

This module was extracted from the production bot. It preserves the complete
case-intake workflow:

- Collect client and case information through Telegram.
- Create or reuse a Google Drive case folder.
- Resolve the client, case type, judge and client type in Advocate Diaries.
- Create the court case in Advocate Diaries.
- Mirror the client into the local PostgreSQL database.
- Save the local case record and synchronization result.

The module owns its database connection per completed case. It does not use a
global cursor, which prevents stale cursor and reconnect problems.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

import psycopg2
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from advocate_web import AdvocateWeb
from config import AD_EMAIL, AD_PASSWORD, DATABASE_URL
from utils.drive import get_or_create_case_folder


logger = logging.getLogger(__name__)


(
    CLIENT,
    MOBILE,
    ADVOCATEFOR,
    CLIENTTYPE,
    TITLEPETITIONER,
    TITLERESPONDENT,
    CASETYPE,
    COURT,
    JUDGE,
    OPPOSITE,
    HEARING,
    FEE,
    ADVANCE,
    CONFIRM,
) = range(14)


def normalize_mobile_for_matching(value: Any) -> str:
    """Normalize an Indian mobile number for local matching."""
    digits = "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = "91" + digits

    return digits


def upsert_mirrored_client(cursor, client_data: dict[str, Any]) -> int:
    """Create or update the local mirrored client and return its database ID."""
    ad_client_id = client_data.get("ad_client_id")
    client_name = client_data.get("client_name") or "Unknown Client"
    mobile = client_data.get("mobile") or ""
    email = client_data.get("email") or ""
    address = client_data.get("address") or ""

    existing_id = None

    if ad_client_id:
        cursor.execute(
            """
            SELECT id
            FROM clients
            WHERE ad_client_id = %s
            LIMIT 1
            """,
            (ad_client_id,),
        )
        row = cursor.fetchone()
        existing_id = row[0] if row else None

    if not existing_id and mobile:
        cursor.execute(
            """
            SELECT id
            FROM clients
            WHERE
                REGEXP_REPLACE(
                    COALESCE(mobile, ''),
                    '[^0-9]',
                    '',
                    'g'
                ) = %s
                OR
                REGEXP_REPLACE(
                    COALESCE(whatsapp_number, ''),
                    '[^0-9]',
                    '',
                    'g'
                ) = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (mobile, mobile),
        )
        row = cursor.fetchone()
        existing_id = row[0] if row else None

    if not existing_id and client_name:
        cursor.execute(
            """
            SELECT id
            FROM clients
            WHERE LOWER(TRIM(client_name)) = LOWER(TRIM(%s))
            ORDER BY id ASC
            LIMIT 2
            """,
            (client_name,),
        )
        rows = cursor.fetchall()

        if len(rows) == 1:
            existing_id = rows[0][0]

    if existing_id:
        cursor.execute(
            """
            UPDATE clients
            SET
                ad_client_id = COALESCE(%s, ad_client_id),
                client_name = COALESCE(NULLIF(%s, ''), client_name),
                mobile = COALESCE(NULLIF(%s, ''), mobile),
                whatsapp_number = COALESCE(
                    whatsapp_number,
                    NULLIF(%s, '')
                ),
                email = COALESCE(NULLIF(%s, ''), email),
                address = COALESCE(NULLIF(%s, ''), address),
                ad_sync_status = 'MIRRORED',
                ad_synced_at = CURRENT_TIMESTAMP,
                ad_sync_message =
                    'Updated through new-case creation',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
            """,
            (
                ad_client_id,
                client_name,
                mobile,
                mobile,
                email,
                address,
                existing_id,
            ),
        )
        return cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO clients
        (
            ad_client_id,
            client_name,
            mobile,
            whatsapp_number,
            email,
            address,
            ad_sync_status,
            ad_synced_at,
            ad_sync_message
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'MIRRORED',
            CURRENT_TIMESTAMP,
            %s
        )
        RETURNING id
        """,
        (
            ad_client_id,
            client_name,
            mobile or None,
            mobile or None,
            email or None,
            address or None,
            "Created through new-case creation",
        ),
    )

    return cursor.fetchone()[0]


def _normalize_hearing_date(value: str) -> str:
    """Convert accepted user date formats into Advocate Diaries format."""
    stripped_value = value.strip()

    for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(
                stripped_value,
                date_format,
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue

    raise ValueError(
        "Invalid hearing date. Use DD-MM-YYYY, DD/MM/YYYY, "
        "or YYYY-MM-DD."
    )


def _select_judge(
    judges: list[dict[str, Any]],
    judge_input: str,
) -> dict[str, Any]:
    """Select an exact or unique partial judge match."""
    normalized_input = judge_input.strip().lower()

    exact_match = next(
        (
            judge
            for judge in judges
            if str(judge.get("name", "")).strip().lower()
            == normalized_input
        ),
        None,
    )

    if exact_match:
        return exact_match

    partial_matches = [
        judge
        for judge in judges
        if normalized_input
        in str(judge.get("name", "")).strip().lower()
    ]

    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(partial_matches) > 1:
        names = ", ".join(
            str(judge.get("name", ""))
            for judge in partial_matches
        )
        raise ValueError(
            f"Multiple judges found: {names}. "
            "Enter the complete judge name."
        )

    raise ValueError(
        f"No suitable judge match found for: {judge_input}"
    )


def _select_client_type(
    client_types: list[dict[str, Any]],
    client_type_input: str,
) -> dict[str, Any]:
    """Select a client type while tolerating singular/plural differences."""
    normalized_input = client_type_input.strip().upper().rstrip("S")

    matched_type = next(
        (
            item
            for item in client_types
            if str(item.get("name", ""))
            .strip()
            .upper()
            .rstrip("S")
            == normalized_input
        ),
        None,
    )

    if matched_type:
        return matched_type

    available_types = ", ".join(
        str(item.get("name", ""))
        for item in client_types
    )

    raise ValueError(
        f"Exact client type '{client_type_input}' not found. "
        f"Available matches: {available_types}"
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Display the bot startup message."""
    del context
    await update.effective_message.reply_text(
        "Law Office Bot Live\nUse /newcase"
    )


async def newcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Start the new-case conversation."""
    context.user_data.clear()
    await update.effective_message.reply_text("Enter Client Name:")
    return CLIENT


async def client(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["client_name"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Mobile Number:"
    )
    return MOBILE


async def mobile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["mobile"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Advocate For:\n"
        "Example: Petitioner / Respondent / Objector"
    )
    return ADVOCATEFOR


async def advocate_for(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["advocate_for"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Client Type:\n"
        "Example: Petitioner / Respondent / Applicant / Objector"
    )
    return CLIENTTYPE


async def client_type_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["client_type"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Case Title Petitioner:"
    )
    return TITLEPETITIONER


async def title_petitioner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["case_title_petitioner"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Case Title Respondent:"
    )
    return TITLERESPONDENT


async def title_respondent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["case_title_respondent"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text("Enter Case Type:")
    return CASETYPE


async def case_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["case_type"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text("Enter Court Name:")
    return COURT


async def court(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["court_name"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text("Enter Judge Name:")
    return JUDGE


async def judge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["judge_name"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Opposite Party:"
    )
    return OPPOSITE


async def opposite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["opposite_party"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Next Hearing Date:"
    )
    return HEARING


async def hearing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["hearing_date"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Fee Agreed:"
    )
    return FEE


async def fee(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["fee_agreed"] = (
        update.effective_message.text or ""
    ).strip()

    await update.effective_message.reply_text(
        "Enter Advance Received:"
    )
    return ADVANCE


async def advance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["advance_received"] = (
        update.effective_message.text or ""
    ).strip()

    summary = (
        "📋 CONFIRM NEW CASE\n\n"
        f"👤 Client: {context.user_data['client_name']}\n"
        f"📱 Mobile: {context.user_data['mobile']}\n"
        f"⚖️ Advocate For: {context.user_data['advocate_for']}\n"
        f"👤 Client Type: {context.user_data['client_type']}\n"
        f"📌 Title Petitioner: "
        f"{context.user_data['case_title_petitioner']}\n"
        f"📌 Title Respondent: "
        f"{context.user_data['case_title_respondent']}\n"
        f"⚖️ Case Type: {context.user_data['case_type']}\n"
        f"🏛 Court: {context.user_data['court_name']}\n"
        f"👨‍⚖️ Judge: {context.user_data['judge_name']}\n"
        f"👥 Opposite Party: "
        f"{context.user_data['opposite_party']}\n"
        f"📅 Next Hearing: "
        f"{context.user_data['hearing_date']}\n"
        f"💰 Fee Agreed: {context.user_data['fee_agreed']}\n"
        f"💵 Advance Received: "
        f"{context.user_data['advance_received']}\n\n"
        "Type YES to save this case or NO to cancel."
    )

    await update.effective_message.reply_text(summary)
    return CONFIRM


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Cancel the new-case conversation."""
    context.user_data.clear()
    await update.effective_message.reply_text(
        "❌ New case cancelled."
    )
    return ConversationHandler.END


async def confirm_newcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Create the case in Drive, Advocate Diaries and PostgreSQL."""
    answer = (
        update.effective_message.text or ""
    ).strip().lower()

    if answer not in {"yes", "y"}:
        return await cancel(update, context)

    case_id = (
        f"CLA-{datetime.now().year}-"
        f"{random.randint(1000, 9999)}"
    )

    folder_id = None
    folder_link = None
    ad_status = "Not created"
    ad_client_id_for_case = None

    try:
        folder_id, folder_link = get_or_create_case_folder(
            case_id
        )
    except Exception as exc:
        logger.exception(
            "Google Drive folder creation failed for %s",
            case_id,
        )
        await update.effective_message.reply_text(
            "❌ Google Drive folder creation failed.\n"
            f"{type(exc).__name__}: {exc}"
        )
        return ConversationHandler.END

    try:
        if not AD_EMAIL or not AD_PASSWORD:
            raise RuntimeError(
                "AD_EMAIL or AD_PASSWORD is missing."
            )

        ad_web = AdvocateWeb(
            email=AD_EMAIL,
            password=AD_PASSWORD,
        )

        clients = ad_web.search_client(
            context.user_data["client_name"]
        )

        if not clients:
            ad_status = (
                "Failed: Client not found in Advocate Diaries"
            )
        else:
            selected_client = clients[0]
            client_id = selected_client["id"]
            ad_client_id_for_case = str(client_id)

            case_types = ad_web.search_case_type(
                context.user_data["case_type"]
            )

            if not case_types:
                raise ValueError(
                    "Case type not found: "
                    f"{context.user_data['case_type']}"
                )

            selected_case_type = case_types[0]
            case_type_id = selected_case_type["id"]
            case_type_name = selected_case_type["name"]

            judges = ad_web.search_judge(
                context.user_data["judge_name"]
            )

            if not judges:
                raise ValueError(
                    "Judge not found: "
                    f"{context.user_data['judge_name']}"
                )

            selected_judge = _select_judge(
                judges,
                context.user_data["judge_name"],
            )
            judge_id = selected_judge["id"]
            judge_name = selected_judge["name"]

            client_type_input_value = (
                context.user_data["client_type"].strip()
            )

            client_types = ad_web.search_client_type(
                client_type_input_value
            )

            if not client_types:
                raise ValueError(
                    "Client type not found: "
                    f"{client_type_input_value}"
                )

            selected_client_type = _select_client_type(
                client_types,
                client_type_input_value,
            )
            client_type_id = selected_client_type["id"]
            client_type_name = selected_client_type["name"]

            normalized_hearing_date = (
                _normalize_hearing_date(
                    context.user_data["hearing_date"]
                )
            )

            ad_response = ad_web.add_court_case(
                client_id=client_id,
                client_name=context.user_data["client_name"],
                opposite_party=(
                    context.user_data["opposite_party"]
                ),
                case_title_petitioner=(
                    context.user_data[
                        "case_title_petitioner"
                    ]
                ),
                case_title_respondent=(
                    context.user_data[
                        "case_title_respondent"
                    ]
                ),
                client_type_id=client_type_id,
                case_type_id=case_type_id,
                judge_id=judge_id,
                hearing_date=normalized_hearing_date,
                purpose="Appearance",
                advocate_for=(
                    context.user_data["advocate_for"]
                ),
            )

            location = ad_response.headers.get(
                "Location",
                "",
            )

            if (
                ad_response.status_code == 302
                and "/court-cases" in location
            ):
                ad_status = (
                    "✅ Case created successfully\n"
                    f"✅ Client: {selected_client['name']}\n"
                    f"✅ Case Type: {case_type_name} "
                    f"(ID {case_type_id})\n"
                    f"✅ Judge: {judge_name}\n"
                    f"✅ Client Type: {client_type_name} "
                    f"(ID {client_type_id})"
                )
            else:
                ad_status = (
                    "❌ Case creation failed\n"
                    f"Status: {ad_response.status_code}\n"
                    f"Location: {location or 'None'}"
                )

    except Exception as exc:
        logger.exception(
            "Advocate Diaries case creation failed for %s",
            case_id,
        )
        ad_status = (
            f"Failed: {type(exc).__name__}: {exc}"
        )

    ad_sync_status = (
        "SUCCESS"
        if "Case created successfully" in ad_status
        else "FAILED"
    )

    case_title_value = (
        f"{context.user_data['case_title_petitioner']} "
        f"VS "
        f"{context.user_data['case_title_respondent']}"
    )

    connection = None

    try:
        connection = psycopg2.connect(DATABASE_URL)

        with connection.cursor() as cursor:
            client_local_id = upsert_mirrored_client(
                cursor,
                {
                    "ad_client_id": ad_client_id_for_case,
                    "client_name": (
                        context.user_data["client_name"]
                    ),
                    "mobile": normalize_mobile_for_matching(
                        context.user_data["mobile"]
                    ),
                    "email": "",
                    "address": "",
                },
            )

            cursor.execute(
                """
                INSERT INTO cases
                (
                    case_id,
                    client_id,
                    ad_client_id,
                    client_name,
                    mobile,
                    case_type,
                    court_name,
                    judge_name,
                    opposite_party,
                    case_title,
                    hearing_date,
                    next_hearing,
                    fee_agreed,
                    advance_received,
                    drive_folder_id,
                    drive_folder_link,
                    ad_sync_status,
                    ad_created_at,
                    ad_sync_message
                )
                VALUES
                (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    case_id,
                    client_local_id,
                    ad_client_id_for_case,
                    context.user_data["client_name"],
                    context.user_data["mobile"],
                    context.user_data["case_type"],
                    context.user_data["court_name"],
                    context.user_data["judge_name"],
                    context.user_data["opposite_party"],
                    case_title_value,
                    context.user_data["hearing_date"],
                    context.user_data["hearing_date"],
                    context.user_data["fee_agreed"],
                    context.user_data["advance_received"],
                    folder_id,
                    folder_link,
                    ad_sync_status,
                    (
                        datetime.now()
                        if ad_sync_status == "SUCCESS"
                        else None
                    ),
                    ad_status,
                ),
            )

        connection.commit()

    except Exception as exc:
        if connection is not None:
            connection.rollback()

        logger.exception(
            "Local case save failed for %s",
            case_id,
        )

        await update.effective_message.reply_text(
            "❌ The case could not be saved locally.\n"
            f"{type(exc).__name__}: {exc}"
        )
        return ConversationHandler.END

    finally:
        if connection is not None:
            connection.close()

    await update.effective_message.reply_text(
        "✅ Case Saved Successfully\n\n"
        f"Case ID: {case_id}\n"
        f"Client: {context.user_data['client_name']}\n"
        f"Mobile: {context.user_data['mobile']}\n"
        f"Advocate For: "
        f"{context.user_data['advocate_for']}\n"
        f"Client Type Entered: "
        f"{context.user_data['client_type']}\n"
        f"Case Title Petitioner: "
        f"{context.user_data['case_title_petitioner']}\n"
        f"Case Title Respondent: "
        f"{context.user_data['case_title_respondent']}\n"
        f"Case Type: {context.user_data['case_type']}\n"
        f"Court: {context.user_data['court_name']}\n"
        f"Judge: {context.user_data['judge_name']}\n"
        f"Opposite Party: "
        f"{context.user_data['opposite_party']}\n"
        f"Next Hearing: "
        f"{context.user_data['hearing_date']}\n"
        f"\n📁 Drive Folder:\n"
        f"{folder_link or 'Not created'}"
        f"\n\n📘 Advocate Diaries:\n{ad_status}"
    )

    context.user_data.clear()
    return ConversationHandler.END


def build_new_case_conversation_handler() -> ConversationHandler:
    """Return the complete `/newcase` Telegram conversation handler."""
    text_input = filters.TEXT & ~filters.COMMAND

    return ConversationHandler(
        entry_points=[
            CommandHandler("newcase", newcase),
        ],
        states={
            CLIENT: [MessageHandler(text_input, client)],
            MOBILE: [MessageHandler(text_input, mobile)],
            ADVOCATEFOR: [
                MessageHandler(text_input, advocate_for)
            ],
            CLIENTTYPE: [
                MessageHandler(text_input, client_type_input)
            ],
            TITLEPETITIONER: [
                MessageHandler(text_input, title_petitioner)
            ],
            TITLERESPONDENT: [
                MessageHandler(text_input, title_respondent)
            ],
            CASETYPE: [
                MessageHandler(text_input, case_type)
            ],
            COURT: [MessageHandler(text_input, court)],
            JUDGE: [MessageHandler(text_input, judge)],
            OPPOSITE: [
                MessageHandler(text_input, opposite)
            ],
            HEARING: [
                MessageHandler(text_input, hearing)
            ],
            FEE: [MessageHandler(text_input, fee)],
            ADVANCE: [
                MessageHandler(text_input, advance)
            ],
            CONFIRM: [
                MessageHandler(text_input, confirm_newcase)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )
