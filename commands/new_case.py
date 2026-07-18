"""
Exact production extraction of the `/newcase` workflow.
"""

import os
import random
from datetime import datetime

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
from config import DATABASE_URL
from utils.drive import get_or_create_case_folder


conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()


CLIENT, MOBILE, ADVOCATEFOR, CLIENTTYPE, TITLEPETITIONER, TITLERESPONDENT, CASETYPE, COURT, JUDGE, OPPOSITE, HEARING, FEE, ADVANCE, CONFIRM = range(14)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Law Office Bot Live\nUse /newcase")

async def newcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Enter Client Name:"
    )
    return CLIENT

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["client_name"]=update.message.text; await update.message.reply_text("Enter Mobile Number:"); return MOBILE

async def mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mobile"] = update.message.text.strip()

    await update.message.reply_text(
        "Enter Advocate For:\n"
        "Example: Petitioner / Respondent / Objector"
    )

    return ADVOCATEFOR

async def advocate_for(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["advocate_for"] = update.message.text.strip()

    await update.message.reply_text(
        "Enter Client Type:\n"
        "Example: Petitioner / Respondent / Applicant / Objector"
    )

    return CLIENTTYPE

async def client_type_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_type"] = update.message.text.strip()

    await update.message.reply_text(
        "Enter Case Title Petitioner:"
    )

    return TITLEPETITIONER

async def title_petitioner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["case_title_petitioner"] = update.message.text.strip()

    await update.message.reply_text(
        "Enter Case Title Respondent:"
    )

    return TITLERESPONDENT

async def title_respondent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["case_title_respondent"] = update.message.text.strip()

    await update.message.reply_text(
        "Enter Case Type:"
    )

    return CASETYPE

async def case_type(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["case_type"]=update.message.text; await update.message.reply_text("Enter Court Name:"); return COURT

async def court(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["court_name"] = update.message.text
    await update.message.reply_text("Enter Judge Name:")
    return JUDGE

async def judge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["judge_name"] = update.message.text
    await update.message.reply_text("Enter Opposite Party:")
    return OPPOSITE

async def opposite(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["opposite_party"]=update.message.text; await update.message.reply_text("Enter Next Hearing Date:"); return HEARING

async def hearing(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["hearing_date"]=update.message.text; await update.message.reply_text("Enter Fee Agreed:"); return FEE

async def fee(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["fee_agreed"]=update.message.text; await update.message.reply_text("Enter Advance Received:"); return ADVANCE

async def advance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["advance_received"] = update.message.text

    summary = (
        "📋 CONFIRM NEW CASE\n\n"
        f"👤 Client: {context.user_data['client_name']}\n"
        f"📱 Mobile: {context.user_data['mobile']}\n"
        f"⚖️ Advocate For: {context.user_data['advocate_for']}\n"
        f"👤 Client Type: {context.user_data['client_type']}\n"
        f"📌 Title Petitioner: {context.user_data['case_title_petitioner']}\n"
        f"📌 Title Respondent: {context.user_data['case_title_respondent']}\n"
        f"⚖️ Case Type: {context.user_data['case_type']}\n"
        f"🏛 Court: {context.user_data['court_name']}\n"
        f"👨‍⚖️ Judge: {context.user_data['judge_name']}\n"
        f"👥 Opposite Party: {context.user_data['opposite_party']}\n"
        f"📅 Next Hearing: {context.user_data['hearing_date']}\n"
        f"💰 Fee Agreed: {context.user_data['fee_agreed']}\n"
        f"💵 Advance Received: {context.user_data['advance_received']}\n\n"
        "Type YES to save this case or NO to cancel."
    )

    await update.message.reply_text(summary)
    return CONFIRM

async def confirm_newcase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().lower()

    if answer not in ["yes", "y"]:
        await update.message.reply_text("❌ New case cancelled.")
        return ConversationHandler.END

    case_id = f"CLA-2026-{random.randint(1000,9999)}"
    folder_id, folder_link = get_or_create_case_folder(case_id)

    ad_status = "Not created"
    ad_client_id_for_case = None

    try:
        ad_web = AdvocateWeb(
            email=os.getenv("AD_EMAIL"),
            password=os.getenv("AD_PASSWORD")
        )

        clients = ad_web.search_client(
            context.user_data["client_name"]
        )

        if not clients:
            ad_status = "Failed: Client not found in Advocate Diaries"
        else:
            client_id = clients[0]["id"]
            ad_client_id_for_case = str(client_id)


            case_types = ad_web.search_case_type(
                context.user_data["case_type"]
            )

            if not case_types:
                raise Exception(
                    f"Case type not found: {context.user_data['case_type']}"
                )

            case_type_id = case_types[0]["id"]
            case_type_name = case_types[0]["name"]


            judges = ad_web.search_judge(
                context.user_data["judge_name"]
            )

            if not judges:
                raise Exception(
                    f"Judge not found: {context.user_data['judge_name']}"
                )

            judge_input = context.user_data["judge_name"].strip().lower()

            exact_judge = next(
                (
                    j for j in judges
                    if j["name"].strip().lower() == judge_input
                ),
                None
            )

            if exact_judge:
                selected_judge = exact_judge
            else:
                partial_matches = [
                    j for j in judges
                    if judge_input in j["name"].strip().lower()
                ]

                if len(partial_matches) == 1:
                    selected_judge = partial_matches[0]

                elif len(partial_matches) > 1:
                    names = ", ".join(
                        j["name"] for j in partial_matches
                    )
                    raise Exception(
                        f"Multiple judges found: {names}. "
                        f"Enter the complete judge name."
                    )

                else:
                    raise Exception(
                        f"No suitable judge match found for: "
                        f"{context.user_data['judge_name']}"
                    )

            judge_id = selected_judge["id"]
            judge_name = selected_judge["name"]
            

            client_types = ad_web.search_client_type(
                context.user_data["client_type"]
        )
            client_type_match = next(
                (
                    item for item in client_types
                    if item["name"].strip().upper() == context.user_data["client_type"].strip().upper()
                ),
                None
            )

            client_type_input_value = context.user_data["client_type"].strip()

            client_types = ad_web.search_client_type(
                client_type_input_value
            )

            if not client_types:
                raise Exception(
                    f"Client type not found: {client_type_input_value}"
                )

            normalized_input = client_type_input_value.upper().rstrip("S")

            client_type_match = next(
                (
                    item for item in client_types
                    if item["name"].strip().upper().rstrip("S") == normalized_input
                ),
                None
            )

            if not client_type_match:
                available_types = ", ".join(
                    item["name"] for item in client_types
                )

                raise Exception(
                    f"Exact client type '{client_type_input_value}' not found. "
                    f"Available matches: {available_types}"
                )

            client_type_id = client_type_match["id"]
            client_type_name = client_type_match["name"]

            
            hearing_input = context.user_data["hearing_date"].strip()

            normalized_hearing_date = None

            for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]:
                try:
                    normalized_hearing_date = datetime.strptime(
                        hearing_input,
                        fmt
                    ).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

            if normalized_hearing_date is None:
                raise Exception(
                    "Invalid hearing date. Use DD-MM-YYYY, DD/MM/YYYY, or YYYY-MM-DD."
                )

            ad_response = ad_web.add_court_case(
                client_id=client_id,
                client_name=context.user_data["client_name"],
                opposite_party=context.user_data["opposite_party"],
                case_title_petitioner=context.user_data["case_title_petitioner"],
                case_title_respondent=context.user_data["case_title_respondent"],
                client_type_id=client_type_id,
                case_type_id=case_type_id,
                judge_id=judge_id,
                hearing_date=normalized_hearing_date,
                purpose="Appearance",
                advocate_for=context.user_data["advocate_for"]
            )
            if (
                ad_response.status_code == 302
                and "/court-cases" in ad_response.headers.get("Location", "")
            ):
                ad_status = (
                    f"✅ Case created successfully\n"
                    f"✅ Client: {clients[0]['name']}\n"
                    f"✅ Case Type: {case_type_name} (ID {case_type_id})\n"
                    f"✅ Judge: {judge_name}\n"
                    f"✅ Client Type: {client_type_name} (ID {client_type_id})"
                )
            else:
                ad_status = (
                    f"❌ Case creation failed\n"
                    f"Status: {ad_response.status_code}\n"
                    f"Location: {ad_response.headers.get('Location', 'None')}"
                )

    except Exception as e:
        ad_status = f"Failed: {type(e).__name__}: {e}"    

    ad_sync_status = "FAILED"
    ad_sync_message = ad_status

    if "Case created successfully" in ad_status:
        ad_sync_status = "SUCCESS"
    
    case_title_value = (
        f"{context.user_data['case_title_petitioner']} "
        f"VS {context.user_data['case_title_respondent']}"
    )

    client_local_id = upsert_mirrored_client(cur, {
        "ad_client_id": ad_client_id_for_case,
        "client_name": context.user_data["client_name"],
        "mobile": normalize_mobile_for_matching(
            context.user_data["mobile"]
        ),
        "email": "",
        "address": "",
    })

    cur.execute("""
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
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
    """, (
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
        datetime.now() if ad_sync_status == "SUCCESS" else None,
        ad_sync_message
    ))

    conn.commit()

    await update.message.reply_text(
        f"✅ Case Saved Successfully\n\n"
        f"Case ID: {case_id}\n"
        f"Client: {context.user_data['client_name']}\n"
        f"Mobile: {context.user_data['mobile']}\n"
        f"Advocate For: {context.user_data['advocate_for']}\n"
        f"Client Type Entered: {context.user_data['client_type']}\n"
        f"Case Title Petitioner: {context.user_data['case_title_petitioner']}\n"
        f"Case Title Respondent: {context.user_data['case_title_respondent']}\n"
        f"Case Type: {context.user_data['case_type']}\n"
        f"Court: {context.user_data['court_name']}\n"
        f"Judge: {context.user_data['judge_name']}\n"
        f"Opposite Party: {context.user_data['opposite_party']}\n"
        f"Next Hearing: {context.user_data['hearing_date']}"
        f"\n📁 Drive Folder:\n{folder_link}"
        f"\n\n📘 Advocate Diaries:\n{ad_status}"   
    )

    return ConversationHandler.END

def normalize_mobile_for_matching(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = "91" + digits

    return digits

def upsert_mirrored_client(cur, client_data):
    ad_client_id = client_data.get("ad_client_id")
    client_name = client_data.get("client_name") or "Unknown Client"
    mobile = client_data.get("mobile") or ""
    email = client_data.get("email") or ""
    address = client_data.get("address") or ""

    existing_id = None

    if ad_client_id:
        cur.execute(
            "SELECT id FROM clients WHERE ad_client_id = %s LIMIT 1",
            (ad_client_id,)
        )
        row = cur.fetchone()
        existing_id = row[0] if row else None

    if not existing_id and mobile:
        cur.execute("""
            SELECT id
            FROM clients
            WHERE REGEXP_REPLACE(COALESCE(mobile, ''), '[^0-9]', '', 'g') = %s
               OR REGEXP_REPLACE(COALESCE(whatsapp_number, ''), '[^0-9]', '', 'g') = %s
            ORDER BY id ASC
            LIMIT 1
        """, (mobile, mobile))
        row = cur.fetchone()
        existing_id = row[0] if row else None

    if not existing_id and client_name:
        cur.execute("""
            SELECT id
            FROM clients
            WHERE LOWER(TRIM(client_name)) = LOWER(TRIM(%s))
            ORDER BY id ASC
            LIMIT 2
        """, (client_name,))
        rows = cur.fetchall()
        if len(rows) == 1:
            existing_id = rows[0][0]

    if existing_id:
        cur.execute("""
            UPDATE clients
            SET
                ad_client_id = COALESCE(%s, ad_client_id),
                client_name = COALESCE(NULLIF(%s, ''), client_name),
                mobile = COALESCE(NULLIF(%s, ''), mobile),
                whatsapp_number = COALESCE(whatsapp_number, NULLIF(%s, '')),
                email = COALESCE(NULLIF(%s, ''), email),
                address = COALESCE(NULLIF(%s, ''), address),
                ad_sync_status = 'MIRRORED',
                ad_synced_at = CURRENT_TIMESTAMP,
                ad_sync_message = 'Updated through Advocate Diaries case sync',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
        """, (
            ad_client_id,
            client_name,
            mobile,
            mobile,
            email,
            address,
            existing_id
        ))
        return cur.fetchone()[0]

    cur.execute("""
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
        VALUES (%s, %s, %s, %s, %s, %s, 'MIRRORED', CURRENT_TIMESTAMP, %s)
        RETURNING id
    """, (
        ad_client_id,
        client_name,
        mobile or None,
        mobile or None,
        email or None,
        address or None,
        'Created through Advocate Diaries case sync'
    ))

    return cur.fetchone()[0]

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Cancelled"); return ConversationHandler.END

def build_new_case_conversation_handler():
    text_input = filters.TEXT & ~filters.COMMAND

    return ConversationHandler(
        entry_points=[CommandHandler("newcase", newcase)],
        states={
            CLIENT: [MessageHandler(text_input, client)],
            MOBILE: [MessageHandler(text_input, mobile)],
            ADVOCATEFOR: [MessageHandler(text_input, advocate_for)],
            CLIENTTYPE: [MessageHandler(text_input, client_type_input)],
            TITLEPETITIONER: [MessageHandler(text_input, title_petitioner)],
            TITLERESPONDENT: [MessageHandler(text_input, title_respondent)],
            CASETYPE: [MessageHandler(text_input, case_type)],
            COURT: [MessageHandler(text_input, court)],
            JUDGE: [MessageHandler(text_input, judge)],
            OPPOSITE: [MessageHandler(text_input, opposite)],
            HEARING: [MessageHandler(text_input, hearing)],
            FEE: [MessageHandler(text_input, fee)],
            ADVANCE: [MessageHandler(text_input, advance)],
            CONFIRM: [MessageHandler(text_input, confirm_newcase)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
