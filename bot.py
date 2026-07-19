import os
import requests
import psycopg2
import random
import asyncio
import threading
from telegram import WebAppInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from attendance_app import run_attendance_app
from commands.attendance import (
    attendance,
    checkin,
    checkout,
    approve_attendance,
    linkstaff,
    teststafflogin,
    monitor_attendance_job
)
from commands.attendance_reports import (
    whoinoffice,
    attendancetoday,
    staffattendance,
    forgot_checkout_job,
    daily_attendance_summary_job,
    test_forgot_checkout,
    test_attendance_summary,
    sync_today_attendance_sessions,
)
from commands.communication import (
    build_communication_conversation_handler,
    communication_callback,
    missingmobiles,
    pendingclientverification,
    confirmclientdetails,
    clientchanges,
    messagehistory,
)

from services.client_timeline import (
    ensure_client_timeline_table,
)

from commands.client_timeline import (
    clienttimeline,
    synctimeline,
    addtimeline,
)



from api_explorer import run_api_explorer
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from commands.works import (
    works,
    work,
    completework,
    assignwork,
    assigntask,
    mytasks,
    completetask,
    pendingtasks,
    stafftasks
)
from commands.tasks import (
    taskdetails,
    taskhistory,
    reassign_task,
    reassign_history,
    reopen_task,
    reopen_history,
    set_task_priority
)
from commands.admin_db import (
    show_case_columns,
    show_table_columns,
    refreshofficeprofile,
)
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters
)
from telegram.ext import CallbackQueryHandler
from case_handlers import register_case_handlers
from config import DATABASE_URL
from advocate_web import AdvocateWeb
from commands.dashboard import (
    morningdashboard,
    morning_dashboard_job,
    staff_morning_brief_job,
    test_staff_morning_briefs
)
from commands.files import (
    WAITING_FILE,
    CONFIRM_DUPLICATE_UPLOAD,
    upload_start,
    upload_category_callback,
    upload_file,
    duplicate_upload_callback,
    cancel_upload,
    casefolder,
    casefiles,
    files,
    latestfiles,
    sharecasefolder,
    filehistory,
    openfile,
    findfile,
)

from commands.ad_sync_v2 import (
    synccasesv2,
    daily_ad_sync_v2_job,
)

from utils.drive import (
    drive_service,
    get_or_create_case_folder,
)

from services.activity_logger import (
    ensure_activity_schema,
)

from services.hearing_automation import (
    ensure_hearing_automation_tables,
)

from commands.hearing_automation import (
    generatehearingreminders,
    hearingqueue,
    hearingpreview,
    hearing_automation_callback,
    hearing_reminder_generation_job,
)

from commands.ad_api_diagnostics import (
    inspectadcase,
    inspectadclient,
)

from commands.ad_sync_v3 import (
    synccasesv3,
    daily_ad_sync_v3_job,
)

from commands.mobile_audit import (
    missingmobilesreport,
    repairmobiles,
    mobileaudit,
)

from commands.mobile_update_queue import (
    mobileupdatequeue,
    mobileupdatequeuesummary,
)

TOKEN = os.getenv("BOT_TOKEN")

AD_API = os.getenv("AD_API")
AD_EMAIL = os.getenv("AD_EMAIL")
AD_PASSWORD = os.getenv("AD_PASSWORD")

ACCESS_TOKEN = None
REFRESH_TOKEN = None
def ad_login():
    global ACCESS_TOKEN, REFRESH_TOKEN

    url = f"{AD_API}/login"

    payload = {
        "email": AD_EMAIL,
        "password": AD_PASSWORD
    }

    try:
        r = requests.post(url, json=payload)

        print("LOGIN STATUS:", r.status_code)
        
        if r.status_code == 200:
            data = r.json()

            ACCESS_TOKEN = data["data"]["access_token"]
            REFRESH_TOKEN = data["data"]["refresh_token"]

            
        else:
            print("Advocate Diaries Login Failed")

    except Exception as e:
        print("LOGIN ERROR:", e)
conn=psycopg2.connect(DATABASE_URL)
cur=conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS cases (
    id SERIAL PRIMARY KEY,
    case_id TEXT,
    client_name TEXT,
    mobile TEXT,
    case_type TEXT,
    court_name TEXT,
    opposite_party TEXT,
    hearing_date TEXT,
    fee_agreed TEXT,
    advance_received TEXT
)
""")

cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS court_name TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS opposite_party TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS hearing_date TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS fee_agreed TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS advance_received TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN'")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS notes TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_id INTEGER")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_client_id TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verification_status TEXT DEFAULT 'NOT_SENT'")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verification_sent_at TIMESTAMP")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verified_at TIMESTAMP")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_correction_note TEXT")

conn.commit()
cur.execute("""
CREATE TABLE IF NOT EXISTS staff (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE,
    role TEXT
)
""")

cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS attendance TEXT")
conn.commit()
cur.execute("""
CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    staff_name TEXT,
    date TEXT,
    status TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS attendance_notifications (
    attendance_id TEXT PRIMARY KEY,
    staff_name TEXT,
    attendance_date TEXT,
    in_time TEXT,
    out_time TEXT,
    approval_status TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    case_number TEXT,
    assigned_to TEXT,
    task TEXT,
    deadline TEXT,
    status TEXT DEFAULT 'PENDING'
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS fee_installments (
    id SERIAL PRIMARY KEY,
    case_number TEXT,
    amount TEXT,
    date TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS case_responsibility (
    id SERIAL PRIMARY KEY,
    case_number TEXT,
    staff_name TEXT,
    responsibility TEXT
)
""")

cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS drive_folder_id TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS drive_folder_link TEXT")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS staff_accounts (
    telegram_user_id BIGINT PRIMARY KEY,
    staff_name TEXT,
    ad_email TEXT NOT NULL,
    ad_password TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()
staff_data = [
    ("Preet", "Office Manager / Law Student"),
    ("Happy", "Final Year Law Student"),
    ("Priya", "Personal Assistant"),
    ("Jimmy", "Clerk")
]
for s in staff_data:
    cur.execute(
        "INSERT INTO staff (name, role) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        s
    )

cur.execute("""
CREATE TABLE IF NOT EXISTS attendance_locations (
    id SERIAL PRIMARY KEY,
    staff_name TEXT,
    telegram_user_id BIGINT,
    action TEXT,
    latitude TEXT,
    longitude TEXT,
    map_link TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS attendance_offices (
    id SERIAL PRIMARY KEY,
    office_name TEXT NOT NULL UNIQUE,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    allowed_radius_meters INTEGER DEFAULT 300,
    allow_checkin BOOLEAN DEFAULT TRUE,
    allow_checkout BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
ALTER TABLE attendance_locations
ADD COLUMN IF NOT EXISTS office_id INTEGER
""")

cur.execute("""
ALTER TABLE attendance_locations
ADD COLUMN IF NOT EXISTS office_name TEXT
""")

cur.execute("""
ALTER TABLE attendance_locations
ADD COLUMN IF NOT EXISTS distance_meters DOUBLE PRECISION
""")

cur.execute("""
ALTER TABLE attendance_locations
ADD COLUMN IF NOT EXISTS accuracy_meters DOUBLE PRECISION
""")
cur.execute("""
INSERT INTO attendance_offices
(
    office_name,
    latitude,
    longitude,
    allowed_radius_meters,
    allow_checkin,
    allow_checkout,
    is_active
)
VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE)
ON CONFLICT (office_name)
DO UPDATE SET
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    allowed_radius_meters =
        EXCLUDED.allowed_radius_meters,
    allow_checkin = TRUE,
    allow_checkout = TRUE,
    is_active = TRUE
""", (
    "Court Chamber Office",
    30.8999606,
    75.8346954,
    300
))
cur.execute("""
INSERT INTO attendance_offices
(
    office_name,
    latitude,
    longitude,
    allowed_radius_meters,
    allow_checkin,
    allow_checkout,
    is_active
)
VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE)
ON CONFLICT (office_name)
DO UPDATE SET
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    allowed_radius_meters =
        EXCLUDED.allowed_radius_meters,
    allow_checkin = TRUE,
    allow_checkout = TRUE,
    is_active = TRUE
""", (
    "Evening Office",
    30.913241,
    75.838635,
    300
))
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id SERIAL PRIMARY KEY,

    telegram_user_id BIGINT NOT NULL,
    staff_name TEXT NOT NULL,
    attendance_date DATE NOT NULL,

    checkin_time TIMESTAMP,
    checkin_office_id INTEGER,
    checkin_office_name TEXT,

    checkout_time TIMESTAMP,
    checkout_office_id INTEGER,
    checkout_office_name TEXT,

    status TEXT DEFAULT 'OPEN',
    working_minutes INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS
attendance_sessions_user_date_idx
ON attendance_sessions
(
    telegram_user_id,
    attendance_date
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS attendance_movements (
    id SERIAL PRIMARY KEY,
    attendance_session_id INTEGER,
    telegram_user_id BIGINT NOT NULL,
    staff_name TEXT NOT NULL,

    from_office_id INTEGER,
    from_office_name TEXT,

    to_office_id INTEGER NOT NULL,
    to_office_name TEXT NOT NULL,

    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy_meters DOUBLE PRECISION,
    distance_meters DOUBLE PRECISION,
    map_link TEXT,

    moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
ALTER TABLE attendance_sessions
ADD COLUMN IF NOT EXISTS current_office_id INTEGER
""")

cur.execute("""
ALTER TABLE attendance_sessions
ADD COLUMN IF NOT EXISTS current_office_name TEXT
""")

conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS case_files (
    id SERIAL PRIMARY KEY,
    case_id TEXT,
    file_name TEXT,
    drive_file_id TEXT,
    drive_file_link TEXT,
    uploaded_by BIGINT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS office_profile (
    id SERIAL PRIMARY KEY,
    office_name TEXT NOT NULL,
    office_whatsapp TEXT,
    office_phone TEXT,
    office_email TEXT,
    court_office_address TEXT,
    evening_office_address TEXT,
    office_hours TEXT,
    website TEXT,
    court_maps_link TEXT,
    evening_maps_link TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS
office_profile_one_active_idx
ON office_profile ((is_active))
WHERE is_active = TRUE
""")

cur.execute("""
INSERT INTO office_profile
(
    office_name,
    office_whatsapp,
    office_phone,
    office_email,
    court_office_address,
    evening_office_address,
    office_hours,
    website,
    court_maps_link,
    evening_maps_link,
    is_active
)
SELECT
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE
WHERE NOT EXISTS (
    SELECT 1
    FROM office_profile
    WHERE is_active = TRUE
)
""", (
    os.getenv("OFFICE_NAME", "Law Office of Ajay Chawla"),
    os.getenv("OFFICE_WHATSAPP_NUMBER"),
    os.getenv("OFFICE_PHONE_NUMBER"),
    os.getenv("OFFICE_EMAIL"),
    os.getenv("COURT_OFFICE_ADDRESS", "District Courts, Ludhiana"),
    os.getenv("EVENING_OFFICE_ADDRESS"),
    os.getenv("OFFICE_HOURS", "Monday-Saturday, 9:30 AM-6:30 PM"),
    os.getenv("OFFICE_WEBSITE"),
    os.getenv("COURT_OFFICE_MAPS_LINK"),
    os.getenv("EVENING_OFFICE_MAPS_LINK")
))

cur.execute("""
CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    ad_client_id TEXT UNIQUE,
    client_name TEXT NOT NULL,
    mobile TEXT,
    whatsapp_number TEXT,
    email TEXT,
    address TEXT,
    verification_status TEXT DEFAULT 'NOT_SENT',
    verification_sent_at TIMESTAMP,
    verified_at TIMESTAMP,
    correction_note TEXT,
    ad_sync_status TEXT DEFAULT 'PENDING',
    ad_synced_at TIMESTAMP,
    ad_sync_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE INDEX IF NOT EXISTS clients_mobile_idx
ON clients (mobile)
""")

cur.execute("""
CREATE INDEX IF NOT EXISTS clients_name_idx
ON clients (LOWER(TRIM(client_name)))
""")

# Legacy table is retained only for backward compatibility with older commands.
cur.execute("""
CREATE TABLE IF NOT EXISTS client_contacts (
    id SERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    client_name TEXT,
    whatsapp_number TEXT NOT NULL,
    consent_status TEXT DEFAULT 'UNKNOWN',
    is_primary BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS
client_contacts_case_primary_idx
ON client_contacts (LOWER(TRIM(case_id)))
WHERE is_primary = TRUE
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS client_messages (
    id SERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    client_name TEXT,
    phone_number TEXT NOT NULL,
    channel TEXT DEFAULT 'WHATSAPP',
    message_type TEXT DEFAULT 'CASE_STATUS',
    message_text TEXT NOT NULL,
    sent_by BIGINT,
    delivery_status TEXT DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP
)
""")

cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS client_id INTEGER")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS ad_client_id TEXT")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS communication_ref TEXT")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS template_name TEXT")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS related_case_id TEXT")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS provider_message_id TEXT")
cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS reply_status TEXT")

cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS
client_messages_communication_ref_idx
ON client_messages (communication_ref)
WHERE communication_ref IS NOT NULL
""")

conn.commit()

cur.execute("""
    ALTER TABLE case_files
    ADD COLUMN IF NOT EXISTS category
    TEXT DEFAULT 'MISCELLANEOUS'
""")

cur.execute("""
    ALTER TABLE case_files
    ADD COLUMN IF NOT EXISTS drive_folder_id
    TEXT
""")

conn.commit()

cur.execute("""
    ALTER TABLE case_files
    ADD COLUMN IF NOT EXISTS file_size BIGINT
""")

cur.execute("""
    ALTER TABLE case_files
    ADD COLUMN IF NOT EXISTS sha256_hash TEXT
""")

cur.execute("""
    ALTER TABLE case_files
    ADD COLUMN IF NOT EXISTS telegram_file_unique_id TEXT
""")

conn.commit()

cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_number TEXT")
conn.commit()

cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_at TIMESTAMP")
conn.commit()

cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'manual'")
cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_work_id TEXT")
cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_by BIGINT")
cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notes TEXT")
conn.commit()

cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_sync_status TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_created_at TIMESTAMP")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_sync_message TEXT")
conn.commit()

cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_case_id TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_title TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS judge_name TEXT")
cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_hearing TEXT")
conn.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS sync_logs (
    id SERIAL PRIMARY KEY,
    sync_type TEXT,
    total_fetched INTEGER,
    added_count INTEGER,
    updated_count INTEGER,
    folders_created INTEGER,
    folders_reused INTEGER,
    skipped_count INTEGER,
    status TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()



async def test_web(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        web = AdvocateWeb()

        status, url, data = web.attendance("2026-07-05")

        await update.message.reply_text(
            f"Status: {status}\n\nURL:\n{url}\n\n{data}"
        )

    except Exception as e:
        await update.message.reply_text(str(e))
async def explore(update, context):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "/explore attendance\n"
            "/explore users\n"
            "/explore tasks"
        )

        return

    module = context.args[0].lower()

    
    if module == "attendance":

        ad = AdvocateDiaries()

        status, text = ad.test_attendance_api("2026-07-05")

        await update.message.reply_text(
            f"Status: {status}\n\n{text[:3500]}"
        )

        return

    
    result = run_api_explorer(module)

    await update.message.reply_text(result)

async def test_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        users = ad.get_users()

        await update.message.reply_text(str(users))

    except Exception as e:
        await update.message.reply_text(str(e))
async def todayhearings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import time
    import requests
    from datetime import datetime

    await update.message.reply_text("Fetching hearings...")

    target_date = datetime.now().strftime("%Y-%m-%d")

    if context.args:
        try:
            target_date = datetime.strptime(
                context.args[0],
                "%d-%m-%Y"
            ).strftime("%Y-%m-%d")
        except:
            await update.message.reply_text(
                "Use format: /todayhearings DD-MM-YYYY"
            )  
            return

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    all_cases = []

    for page in range(1, 61):
        try:
            r = requests.get(
                f"{AD_API}/court_cases?page={page}",
                headers=headers,
                timeout=(10, 60)
            )

            if r.status_code == 404:
                break

            if r.status_code != 200:
                await update.message.reply_text(
                    f"Failed at page {page}\nStatus: {r.status_code}"
                )
                return

            data = r.json().get("data", [])

            if not data:
                break

            all_cases.extend(data)

            time.sleep(1)

        except requests.exceptions.Timeout:
            continue

        except requests.exceptions.RequestException:
            continue

    matched_cases = [
        c for c in all_cases
        if c.get("next_date") == target_date
    ]

    if not matched_cases:
        await update.message.reply_text(
            f"No hearings on {target_date}"
        )
        return

    msg = "\n\n".join(
        [
            f"📌 {c['case_number']}\n"
            f"⚖ {c['case_title']}\n"
            f"👨‍⚖ Judge: {c['judge_name']}\n"
            f"📝 Stage: {c['purpose']}\n"
           f"━━━━━━━━━━━━━━"
            for c in matched_cases
        ]
    )

    for i in range(0, len(msg), 3500):
        await update.message.reply_text(msg[i:i+3500])

async def tomorrowcause(update, context):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d-%m-%Y")
    context.args = [tomorrow]
    await todayhearings(update, context)

async def daily_cause_list_job(context):
    from advocate_diaries import AdvocateDiaries
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os

    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")

    if not group_id:
        raise Exception("OFFICE_GROUP_CHAT_ID is missing")

    today_dt = datetime.now(ZoneInfo("Asia/Kolkata"))
    today = today_dt.strftime("%Y-%m-%d")
    display_date = today_dt.strftime("%d-%m-%Y | %A")

    ad = AdvocateDiaries()
    data = ad.daily_cause_list(today)

    if not data.get("success"):
        message = f"❌ Could not fetch Daily Cause List for {display_date}"
    else:
        cause_data = data.get("data", {})
        total_cases = cause_data.get("total_cases", 0)
        groups = cause_data.get("groups", [])

        if total_cases == 0:
            message = (
                f"📅 DAILY CAUSE LIST\n"
                f"{display_date}\n\n"
                f"No cases are listed for today."
            )
        else:
            message = (
                f"📅 DAILY CAUSE LIST\n"
                f"{display_date}\n\n"
                f"Total Cases: {total_cases}\n\n"
            )

            for group in groups:
                group_name = (
                    group.get("court")
                    or group.get("court_name")
                    or group.get("name")
                    or "Court"
                )

                message += f"⚖️ {group_name}\n"

                cases = group.get("cases", [])

                for i, case in enumerate(cases, start=1):
                    case_no = case.get("case_number") or case.get("case_no") or "-"
                    title = case.get("title") or case.get("case_title") or "-"
                    stage = case.get("stage") or case.get("purpose") or "-"
                    time = case.get("time") or case.get("case_time") or "-"

                    message += (
                        f"{i}. {case_no}\n"
                        f"   {title}\n"
                        f"   Stage: {stage}\n"
                        f"   Time: {time}\n\n"
                    )

                message += "\n"

    await context.bot.send_message(
        chat_id=int(group_id),
        text=message[:3900]
    )

async def pending_tasks_summary_job(context):
    from commands.works import pendingtasks, mytasks
    import os
    import psycopg2

    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")

    # ---------------------------------
    # 1. OFFICE GROUP SUMMARY
    # ---------------------------------

    if group_id:

        class GroupMessage:
            async def reply_text(self, text):
                await context.bot.send_message(
                    chat_id=int(group_id),
                    text=text
                )

        class GroupUpdate:
            effective_message = GroupMessage()

        await pendingtasks(GroupUpdate(), context)

    # ---------------------------------
    # 2. GET LINKED ACTIVE STAFF
    # ---------------------------------

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            telegram_user_id,
            staff_name
        FROM staff_accounts
        WHERE is_active = TRUE
          AND telegram_user_id IS NOT NULL
    """)

    staff_rows = cur.fetchall()

    cur.close()
    conn.close()

    # ---------------------------------
    # 3. PRIVATE STAFF SUMMARIES
    # ---------------------------------

    for telegram_user_id, staff_name in staff_rows:

        try:

            class PrivateUser:
                id = telegram_user_id

            class PrivateMessage:
                async def reply_text(self, text):
                    await context.bot.send_message(
                        chat_id=telegram_user_id,
                        text=text
                    )

            class PrivateUpdate:
                effective_user = PrivateUser()
                effective_message = PrivateMessage()

            await mytasks(
                PrivateUpdate(),
                context
            )

        except Exception as e:
            print(
                f"PRIVATE SUMMARY FAILED "
                f"{staff_name} ({telegram_user_id}): "
                f"{type(e).__name__}: {e}"
            )
            
async def test_cause_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "⏳ Testing automatic cause list delivery..."
    )

    try:
        await daily_cause_list_job(context)

        await update.effective_message.reply_text(
            "✅ Test completed. Check the office group."
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Cause list test failed:\n{type(e).__name__}: {e}"
        )

async def test_pending_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("⏳ Testing pending-task summary...")

    try:
        await pending_tasks_summary_job(context)
        await update.effective_message.reply_text(
            "✅ Test completed. Check the office group."
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Pending summary failed:\n{type(e).__name__}: {e}"
        )
        
async def completed_yesterday_summary_job(context):
    import os
    import psycopg2
    from datetime import datetime

    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")

    async def send_long_message(chat_id, message):
        max_length = 3800

        while message:
            if len(message) <= max_length:
                chunk = message
                message = ""
            else:
                split_at = message.rfind(
                    "\n\n",
                    0,
                    max_length
                )

                if split_at == -1:
                    split_at = max_length

                chunk = message[:split_at]
                message = message[split_at:].lstrip()

            await context.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                disable_web_page_preview=True
            )

    def format_date(value, include_time=False):
        if not value:
            return ""

        if isinstance(value, datetime):
            if include_time:
                return value.strftime(
                    "%d-%m-%Y %I:%M %p"
                )

            return value.strftime("%d-%m-%Y")

        return str(value)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                t.id,
                t.assigned_to,
                t.case_number,
                t.task,
                t.deadline,
                t.due_at,
                t.completed_at,
                t.notes,
                t.source_type,
                s.telegram_user_id,
                case_record.client_name,
                case_record.case_title
            FROM tasks t

            LEFT JOIN (
                SELECT DISTINCT ON (
                    LOWER(TRIM(staff_name))
                )
                    staff_name,
                    telegram_user_id
                FROM staff_accounts
                WHERE is_active = TRUE
                ORDER BY
                    LOWER(TRIM(staff_name)),
                    created_at DESC
            ) s
                ON LOWER(TRIM(t.assigned_to))
                   =
                   LOWER(TRIM(s.staff_name))

            LEFT JOIN LATERAL (
                SELECT
                    c.client_name,
                    c.case_title
                FROM cases c
                WHERE
                    t.case_number IS NOT NULL
                    AND TRIM(t.case_number) <> ''
                    AND (
                        LOWER(TRIM(c.case_number))
                            =
                        LOWER(TRIM(t.case_number))

                        OR

                        LOWER(TRIM(c.case_id))
                            =
                        LOWER(TRIM(t.case_number))
                    )
                ORDER BY c.id DESC
                LIMIT 1
            ) case_record
                ON TRUE

            WHERE UPPER(t.status) = 'COMPLETED'
              AND (
                    t.completed_at
                    AT TIME ZONE 'Asia/Kolkata'
                  )::date
                  =
                  (
                    CURRENT_TIMESTAMP
                    AT TIME ZONE 'Asia/Kolkata'
                  )::date - 1

            ORDER BY
                t.assigned_to ASC,
                t.completed_at ASC,
                t.id ASC
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    if not rows:
        if group_id:
            await context.bot.send_message(
                chat_id=int(group_id),
                text=(
                    "✅ YESTERDAY'S COMPLETED TASKS\n\n"
                    "No tasks were completed yesterday."
                )
            )

        return

    grouped = {}

    for (
        task_id,
        staff_name,
        case_number,
        task_text,
        hearing_date,
        due_at,
        completed_at,
        notes,
        source_type,
        telegram_user_id,
        client_name,
        mirrored_case_title
    ) in rows:

        staff_name = staff_name or "Unassigned"

        case_title = (
            notes
            or mirrored_case_title
            or ""
        )

        if staff_name not in grouped:
            grouped[staff_name] = {
                "telegram_user_id": telegram_user_id,
                "tasks": []
            }

        grouped[staff_name]["tasks"].append({
            "id": task_id,
            "client_name": client_name or "",
            "case_title": case_title,
            "case_number": case_number or "",
            "task": task_text or "",
            "hearing_date": hearing_date,
            "due_at": due_at,
            "completed_at": completed_at,
            "source_type": source_type or ""
        })

    def build_task_text(item):
        text = f"🆔 Task #{item['id']}\n"

        if item["client_name"]:
            text += (
                f"👤 Client: "
                f"{item['client_name']}\n"
            )

        if item["case_title"]:
            text += (
                f"⚖️ Case Title: "
                f"{item['case_title']}\n"
            )

        if item["case_number"]:
            text += (
                f"🔢 Case Number: "
                f"{item['case_number']}\n"
            )

        text += (
            f"📝 Task: {item['task']}\n"
        )

        if item["hearing_date"]:
            text += (
                f"📅 Next Hearing: "
                f"{format_date(item['hearing_date'])}\n"
            )

        if item["due_at"]:
            text += (
                f"⏰ Internal Deadline: "
                f"{format_date(item['due_at'], True)}\n"
            )

        if item["completed_at"]:
            text += (
                f"🕒 Completed At: "
                f"{format_date(item['completed_at'], True)}\n"
            )

        if (
            item["source_type"]
            == "advocate_diaries_work"
        ):
            source_label = "Advocate Diaries Work"

        elif item["source_type"] == "manual":
            source_label = "Manual Task"

        else:
            source_label = (
                item["source_type"]
                or "Not recorded"
            )

        text += (
            f"📌 Source: {source_label}\n"
            f"✅ Completed\n\n"
        )

        return text

    # OFFICE GROUP REPORT

    if group_id:
        group_msg = (
            "✅ YESTERDAY'S COMPLETED TASKS "
            "— STAFF WISE\n\n"
        )

        for staff_name, data in grouped.items():
            group_msg += (
                f"👤 {staff_name.upper()}\n"
                f"✅ Completed Tasks: "
                f"{len(data['tasks'])}\n\n"
            )

            for item in data["tasks"]:
                group_msg += build_task_text(item)

            group_msg += (
                "──────────────\n\n"
            )

        await send_long_message(
            int(group_id),
            group_msg
        )

    # PRIVATE STAFF REPORTS

    for staff_name, data in grouped.items():
        telegram_user_id = data[
            "telegram_user_id"
        ]

        if not telegram_user_id:
            continue

        private_msg = (
            "✅ YOUR COMPLETED TASKS "
            "— YESTERDAY\n\n"
            f"👤 {staff_name.upper()}\n"
            f"✅ Completed Tasks: "
            f"{len(data['tasks'])}\n\n"
        )

        for item in data["tasks"]:
            private_msg += build_task_text(item)

        try:
            await send_long_message(
                telegram_user_id,
                private_msg
            )

        except Exception as e:
            print(
                "COMPLETED SUMMARY PRIVATE SEND "
                f"FAILED {staff_name}: "
                f"{type(e).__name__}: {e}"
            )

async def test_completed_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "⏳ Testing yesterday's completed-task summary..."
    )

    try:
        await completed_yesterday_summary_job(context)

        await update.effective_message.reply_text(
            "✅ Test completed. Check the office group "
            "and staff private chats."
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Completed summary failed:\n"
            f"{type(e).__name__}: {e}"
        )
        
async def task_deadline_alert_job(context):
    import os
    import psycopg2
    from config import DATABASE_URL

    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            t.id,
            t.assigned_to,
            t.case_number,
            t.task,
            t.deadline,
            t.due_at,
            t.notes,
            s.telegram_user_id
        FROM tasks t
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(TRIM(staff_name)))
                staff_name,
                telegram_user_id
            FROM staff_accounts
            WHERE is_active = TRUE
            ORDER BY LOWER(TRIM(staff_name)), created_at DESC
        ) s
            ON LOWER(TRIM(t.assigned_to)) =
               LOWER(TRIM(s.staff_name))
        WHERE UPPER(t.status) = 'PENDING'
        ORDER BY t.assigned_to ASC, t.id ASC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    today_expr = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    tomorrow_expr = today_expr + timedelta(days=1)

    grouped = {}

    for task_id, staff, case_no, task, hearing_date, due_at, case_title, telegram_user_id in rows:
        task_date = None

        if due_at:
            task_date = due_at.date()
        elif hearing_date:
            try:
                task_date = datetime.strptime(hearing_date.strip(), "%d-%m-%Y").date()
            except Exception:
                task_date = None

        if not task_date:
            continue

        days_overdue = 0

        if task_date < today_expr:
            days_overdue = (today_expr - task_date).days

            if days_overdue >= 2:
                bucket = "escalated"
            else:
                bucket = "overdue"

        elif task_date == today_expr:
            bucket = "today"

        elif task_date == tomorrow_expr:
            bucket = "tomorrow"

        else:
            continue

        staff = staff or "Unassigned"

        grouped.setdefault(staff, {
            "telegram_user_id": telegram_user_id,
            "escalated": [],
            "overdue": [],
            "today": [],
            "tomorrow": []
        })

        grouped[staff][bucket].append({
            "id": task_id,
            "case_no": case_no,
            "task": task,
            "hearing_date": hearing_date,
            "due_at": due_at,
            "case_title": case_title,
            "days_overdue": days_overdue
        })

    def build_report(title, data):
        msg = f"{title}\n\n"

        for staff, buckets in data.items():
            msg += f"👤 {staff.upper()}\n\n"

            sections = [
                ("🚨 ESCALATED — OVERDUE 2+ DAYS", buckets["escalated"]),
                ("🔴 OVERDUE", buckets["overdue"]),
                ("🟠 DUE TODAY", buckets["today"]),
                ("🟡 DUE TOMORROW", buckets["tomorrow"]),
            ]

            any_task = False

            for section_title, tasks in sections:
                if not tasks:
                    continue

                any_task = True
                msg += f"{section_title}\n"

                for item in tasks:
                    msg += f"🆔 Task #{item['id']}\n"

                    if item["case_title"]:
                        msg += f"⚖️ {item['case_title']}\n"

                    if item["case_no"]:
                        msg += f"🔢 {item['case_no']}\n"

                    msg += f"📝 {item['task']}\n"

                    if item["hearing_date"]:
                        msg += f"📅 Next Hearing: {item['hearing_date']}\n"

                    if item["due_at"]:
                        msg += (
                            f"⏰ Internal Deadline: "
                            f"{item['due_at'].strftime('%d-%m-%Y %I:%M %p')}\n"
                        )

                    if item.get("days_overdue", 0) > 0:
                        msg += f"⏳ Overdue by: {item['days_overdue']} day(s)\n"

                    msg += "\n"

            if not any_task:
                msg += "No urgent tasks.\n"

            msg += "──────────────\n\n"

        return msg

    if not grouped:
        if group_id:
            await context.bot.send_message(
                chat_id=int(group_id),
                text="✅ TASK URGENCY REPORT\n\nNo overdue, due-today, or due-tomorrow pending tasks."
            )
        return

    if group_id:
        group_msg = build_report("⏰ TASK URGENCY REPORT — STAFF WISE", grouped)

        while group_msg:
            chunk = group_msg[:3800]
            group_msg = group_msg[3800:]

            await context.bot.send_message(
                chat_id=int(group_id),
                text=chunk
            )

    for staff, buckets in grouped.items():
        telegram_user_id = buckets["telegram_user_id"]

        if not telegram_user_id:
            continue

        private_grouped = {staff: buckets}
        private_msg = build_report("⏰ YOUR TASK URGENCY REPORT", private_grouped)

        try:
            await context.bot.send_message(
                chat_id=telegram_user_id,
                text=private_msg[:3900]
            )
        except Exception as e:
            print(
                f"URGENCY PRIVATE SEND FAILED "
                f"{staff}: {type(e).__name__}: {e}"
            )
async def test_deadline_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("⏳ Testing deadline alert...")

    try:
        await task_deadline_alert_job(context)
        await update.effective_message.reply_text(
            "✅ Test completed. Check the office group."
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Deadline alert failed:\n{type(e).__name__}: {e}"
        )


async def manual_deadline_5pm_reminder_job(context):
    import os
    import psycopg2
    from config import DATABASE_URL

    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            t.id,
            t.assigned_to,
            t.task,
            t.due_at,
            s.telegram_user_id
        FROM tasks t
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(TRIM(staff_name)))
                staff_name,
                telegram_user_id
            FROM staff_accounts
            WHERE is_active = TRUE
            ORDER BY LOWER(TRIM(staff_name)), created_at DESC
        ) s
            ON LOWER(TRIM(t.assigned_to)) =
               LOWER(TRIM(s.staff_name))
        WHERE UPPER(t.status) = 'PENDING'
          AND t.source_type = 'manual'
          AND t.due_at IS NOT NULL
          AND (t.due_at AT TIME ZONE 'Asia/Kolkata')::date =
              (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date
        ORDER BY t.assigned_to ASC, t.due_at ASC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return

    group_msg = "⏰ 5:00 PM MANUAL TASK DEADLINE REMINDER\n\n"

    staff_msgs = {}

    for task_id, staff, task, due_at, telegram_user_id in rows:
        line = (
            f"🆔 Task #{task_id}\n"
            f"👤 {staff}\n"
            f"📝 {task}\n"
            f"⏰ Deadline: {due_at.strftime('%d-%m-%Y %I:%M %p')}\n\n"
        )

        group_msg += line

        if telegram_user_id:
            staff_msgs.setdefault(
                telegram_user_id,
                f"⏰ YOUR 5:00 PM DEADLINE REMINDER\n\n"
            )
            staff_msgs[telegram_user_id] += line

    if group_id:
        await context.bot.send_message(
            chat_id=int(group_id),
            text=group_msg[:3900]
        )

    for telegram_user_id, msg in staff_msgs.items():
        await context.bot.send_message(
            chat_id=telegram_user_id,
            text=msg[:3900]
        )

async def test_manual_deadline_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "⏳ Testing 5:00 PM manual deadline reminder..."
    )

    try:
        await manual_deadline_5pm_reminder_job(context)
        await update.effective_message.reply_text(
            "✅ Test completed. Check office group and staff private chats."
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Manual deadline reminder failed:\n"
            f"{type(e).__name__}: {e}"
        )
        

async def test_ad_web_create_case(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        ad_email = os.getenv("AD_EMAIL")
        ad_password = os.getenv("AD_PASSWORD")

        test_web = AdvocateWeb(
            email=ad_email,
            password=ad_password
        )

        response = test_web.add_court_case_test()

        await update.effective_message.reply_text(
            f"Status: {response.status_code}\n"
            f"Location: {response.headers.get('Location', 'None')}\n\n"
            f"Response:\n{response.text[:2500]}"
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Advocate Diaries case creation test failed:\n"
            f"{type(e).__name__}: {e}"
        )
        
async def test_ad_web_create_real_case(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        test_web = AdvocateWeb(
            email=os.getenv("AD_EMAIL"),
            password=os.getenv("AD_PASSWORD")
        )

        response = test_web.add_court_case(
            client_id="9d2b34e2-4ddd-48a0-baac-ba967cf8e9e4",
            client_name="BOT REAL METHOD TEST",
            opposite_party="OPPOSITE TEST",
            client_type_id="16",
            case_type_id="11",
            judge_id="058bc666-fb85-49dc-80c0-5c96774ba80b",
            hearing_date="2026-07-08",
            purpose="Appearance"
        )

        await update.effective_message.reply_text(
            f"Status: {response.status_code}\n"
            f"Location: {response.headers.get('Location', 'None')}"
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Real case method test failed:\n"
            f"{type(e).__name__}: {e}"
        )
        

async def debugcasejson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/debugcasejson CASE_NUMBER"
        )
        return

    case_number = " ".join(context.args)

    ad_login()

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    r = requests.get(
        f"{AD_API}/court_cases?search={case_number}",
        headers=headers
    )

    if r.status_code != 200:
        await update.message.reply_text(
            f"Fetch failed. Status: {r.status_code}\n{r.text[:1000]}"
        )
        return

    data = r.json().get("data", [])

    if not data:
        await update.message.reply_text("No case found.")
        return

    import json

    await update.message.reply_text(
        json.dumps(
            data[0],
            indent=2,
            default=str
        )[:3900]
    )
    
async def commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
📘 LAW OFFICE BOT COMMANDS

⚖️ CASES & HEARINGS

/todayhearings
View today's hearings.

/tomorrowcause
View tomorrow's cause list.

/findcase CASE_NUMBER
Find a case.

/case CASE_NUMBER
View case details.

/pendingcases
View pending cases.

/casefolder CASE_NUMBER
Create a Google Drive folder for a case.

/upload CASE_ID
Upload a PDF, Word document, or photo to the case Drive folder.

📂 DOCUMENT MANAGEMENT

/upload CASE_NUMBER
Upload a PDF, Word document, or photo.

/files CASE_NUMBER
List uploaded documents for a case.

/casefiles CASE_NUMBER
Alias for /files.

/openfile FILE_ID
Open a document by file ID.

/findfile KEYWORD
Search uploaded documents.

/findfile CASE_NUMBER | KEYWORD
Search within one case.

/latestfiles
View the 10 latest uploaded documents.

/latestfiles 20
View the latest 20 documents.

/sharecasefolder CASE_NUMBER
Get the Google Drive case-folder link.

/filehistory CASE_NUMBER
View the document upload history.

📱 CLIENT COMMUNICATION

/sendcasestatus CASE_NUMBER
Prepare a reviewable WhatsApp case-status message.

/clientphone CASE_NUMBER PHONE
Temporarily update a case WhatsApp number (legacy compatibility).

/searchcase NAME
Search Advocate Diaries cases by party/client/case keyword.
Example: /searchcase indostar

👤 ATTENDANCE

/linkstaff STAFF EMAIL PASSWORD
Link your staff account.

/checkin
Mark attendance check-in.

/checkout
Mark attendance check-out.

/attendance YYYY-MM-DD
View attendance.

/approveattendance NUMBER
Approve attendance.


⚖️ ADVOCATE DIARIES WORKS

/works
View available works.

/work NUMBER
View a particular work.

/assignwork STAFF NUMBER NUMBER
Assign Advocate Diaries work.

/completework NUMBER
Complete Advocate Diaries work.


📋 TASK MANAGEMENT

/assigntask STAFF TASK | DD-MM-YYYY 6:00 PM
Assign a manual task with optional deadline.

/mytasks
View your pending tasks.

/stafftasks STAFF
View pending tasks of a staff member.

/pendingtasks
View all pending tasks staff-wise.

/completetask TASK_ID
Mark your assigned task as completed.


💰 FEES & PAYMENTS

/pendingfees
View pending fees.

/balance CASE_NUMBER
View case balance.

/addpayment
Add payment.

/closecase
Close a case.


📝 CASE NOTES & RESPONSIBILITY

/addnote
Add case note.

/assignresponsibility
Assign case responsibility.


🔄 SYNCHRONIZATION & UTILITIES

/synccases
Synchronize cases.

/mychatid
View Telegram chat ID.

/explore
API explorer.

/commands
Show this command guide.


🧪 ADMIN TEST COMMANDS

/testcausejob
Test automatic cause-list delivery.

/testpendingsummary
Test pending-task summary.

/testcompletedsummary
Test completed-task summary.

/testdeadlinealert
Test urgency and deadline alerts.

/testmanualdeadline
Test 5:00 PM manual deadline reminder.
"""

    await update.effective_message.reply_text(msg)
async def pendingfees(update: Update, context: ContextTypes.DEFAULT_TYPE): cur.execute("SELECT case_id, client_name, fee_agreed, advance_received FROM cases"); results=cur.fetchall(); msg="\n".join([f"{r[0]} | {r[1]} | Fee:{r[2]} | Advance:{r[3]}" for r in results if r[2]!=r[3]]) if results else "No pending fees"; await update.message.reply_text(msg)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE): case_id=context.args[0] if len(context.args)>0 else None; cur.execute("SELECT fee_agreed, advance_received FROM cases WHERE case_id=%s",(case_id,)) if case_id else None; result=cur.fetchone() if case_id else None; bal=int(result[0])-int(result[1]) if result else 0; await update.message.reply_text(f"Balance Due: {bal}") if result else await update.message.reply_text("Case not found.")

async def closecase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Use /closecase CASEID")
        return

    case_id = context.args[0]

    cur.execute(
        "UPDATE cases SET status='CLOSED' WHERE case_id=%s",
        (case_id,)
    )

    conn.commit()

    await update.message.reply_text(
        f"Case {case_id} closed successfully."
    )
async def addnote(update: Update, context: ContextTypes.DEFAULT_TYPE): case_id=context.args[0] if len(context.args)>1 else None; note=" ".join(context.args[1:]) if len(context.args)>1 else None; cur.execute("UPDATE cases SET notes=%s WHERE case_id=%s",(note,case_id)) if case_id else None; conn.commit() if case_id else None; await update.message.reply_text("Note saved.") if case_id else await update.message.reply_text("Use /addnote CASEID note")
async def addpayment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Use: /addpayment CASEID AMOUNT")
        return

    case_id = context.args[0]
    amount = int(context.args[1])

    cur.execute("SELECT advance_received FROM cases WHERE case_id=%s", (case_id,))
    result = cur.fetchone()

    if not result:
        await update.message.reply_text("Case not found.")
        return

    current_advance = int(result[0])
    new_advance = current_advance + amount

    cur.execute(
        "UPDATE cases SET advance_received=%s WHERE case_id=%s",
        (str(new_advance), case_id)
    )
    conn.commit()

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
    
async def findcase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Use: /findcase CASEID\n"
            "Example: /findcase CLA-2026-9500"
        )
        return

    case_id = context.args[0].strip()

    cur.execute("""
        SELECT
            case_id,
            client_name,
            mobile,
            case_type,
            court_name,
            opposite_party,
            hearing_date,
            drive_folder_link,
            ad_sync_status,
            ad_created_at,
            ad_sync_message
        FROM cases
        WHERE case_id = %s
    """, (case_id,))

    result = cur.fetchone()

    if not result:
        await update.message.reply_text(
            f"❌ Case not found: {case_id}"
        )
        return

    folder_link = result[7] or "Not created"

    await update.message.reply_text(
        f"📁 CASE FOUND\n\n"
        f"🆔 Case ID: {result[0]}\n"
        f"👤 Client: {result[1]}\n"
        f"📱 Mobile: {result[2]}\n"
        f"⚖️ Type: {result[3]}\n"
        f"🏛 Court: {result[4]}\n"
        f"👥 Opposite: {result[5]}\n"
        f"📅 Hearing: {result[6]}\n\n"
        f"📂 Google Drive Folder:\n"
        f"{folder_link}"
        f"\n📘 Advocate Diaries Sync: {result[8] or 'Not recorded'}\n"
        f"🕒 AD Created At: {result[9] or '-'}\n"
        f"📝 Sync Message:\n{result[10] or '-'}"
    )
    
def normalize_mobile_for_matching(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = "91" + digits

    return digits


def extract_ad_client_data(case_payload):
    client_payload = case_payload.get("client")

    if not isinstance(client_payload, dict):
        client_payload = {}

    ad_client_id = (
        case_payload.get("client_id")
        or case_payload.get("ad_client_id")
        or client_payload.get("id")
        or client_payload.get("client_id")
    )

    client_name = (
        case_payload.get("client_name")
        or client_payload.get("name")
        or client_payload.get("client_name")
        or ""
    ).strip()

    mobile = (
        case_payload.get("mobile")
        or case_payload.get("client_mobile")
        or case_payload.get("phone")
        or client_payload.get("mobile")
        or client_payload.get("phone")
        or ""
    )

    email = (
        case_payload.get("client_email")
        or client_payload.get("email")
        or ""
    )

    address = (
        case_payload.get("client_address")
        or client_payload.get("address")
        or ""
    )

    return {
        "ad_client_id": str(ad_client_id).strip() if ad_client_id else None,
        "client_name": client_name,
        "mobile": normalize_mobile_for_matching(mobile),
        "email": str(email or "").strip(),
        "address": str(address or "").strip(),
    }


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


def backfill_clients_from_cases(cur):
    cur.execute("""
        SELECT
            id,
            client_name,
            mobile,
            ad_client_id
        FROM cases
        WHERE client_name IS NOT NULL
          AND TRIM(client_name) <> ''
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    for case_db_id, client_name, mobile, ad_client_id in rows:
        client_local_id = upsert_mirrored_client(cur, {
            "ad_client_id": ad_client_id,
            "client_name": client_name,
            "mobile": normalize_mobile_for_matching(mobile),
            "email": "",
            "address": "",
        })

        cur.execute("""
            UPDATE cases
            SET client_id = %s
            WHERE id = %s
              AND client_id IS NULL
        """, (client_local_id, case_db_id))


def run_synccases_blocking():
    sync_conn = psycopg2.connect(DATABASE_URL)
    cur = sync_conn.cursor()
    conn = sync_conn

    ad_login()

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    all_cases = []
    page = 1

    while True:
        try:
            r = requests.get(
                f"{AD_API}/court_cases?page={page}",
                headers=headers,
                timeout=(10, 45)
            )

        except requests.exceptions.Timeout:
            raise Exception(f"Sync timeout at page {page}")

        except requests.exceptions.RequestException as e:
            raise Exception(
                f"Network error at page {page}: "
                f"{type(e).__name__}: {e}"
            )

        if r.status_code == 404:
            break

        if r.status_code != 200:
            raise Exception(
                f"Sync stopped at page {page}. "
                f"Status: {r.status_code}"
            )

        data = r.json().get("data", [])

        if not data:
            break

        all_cases.extend(data)
        page += 1

    added = 0
    updated = 0
    folders_created = 0
    folders_reused = 0
    skipped = 0

    for c in all_cases:
        ad_case_id = c.get("id")
        client_data = extract_ad_client_data(c)
        ad_client_id = client_data.get("ad_client_id")

        case_number = (c.get("case_number") or "").strip()
        case_title = (c.get("case_title") or "").strip()
        client_name = client_data.get("client_name") or (c.get("client_name") or "").strip()
        client_mobile = client_data.get("mobile") or ""
        case_type = (c.get("case_type_name") or "").strip()
        court_name = (c.get("court_name") or "").strip()
        judge_name = (c.get("judge_name") or "").strip()
        opposite_party = (c.get("verses_name") or "").strip()
        next_hearing = c.get("next_date") or ""
        status = c.get("status") or "pending"
        ad_created_at = c.get("created_at") or None

        if not case_number:
            skipped += 1
            continue

        client_local_id = upsert_mirrored_client(
            cur,
            client_data
        )

        cur.execute("""
            SELECT
                id,
                drive_folder_id,
                drive_folder_link
            FROM cases
            WHERE case_number = %s
               OR case_id = %s
            LIMIT 1
        """, (
            case_number,
            case_number
        ))

        existing = cur.fetchone()

        folder_id = None
        folder_link = None

        if existing:
            existing_id = existing[0]
            folder_id = existing[1]
            folder_link = existing[2]

            if folder_id and folder_link:
                folders_reused += 1
            else:
                folder_id, folder_link = get_or_create_case_folder(
                    case_number
                )

                if folder_id:
                    folders_created += 1

        else:
            existing_id = None

            folder_id, folder_link = get_or_create_case_folder(
                case_number
            )

            if folder_id:
                folders_created += 1

        if existing:
            cur.execute("""
                UPDATE cases
                SET
                    ad_case_id = %s,
                    ad_client_id = %s,
                    client_id = %s,
                    case_id = %s,
                    case_number = %s,
                    case_title = %s,
                    client_name = %s,
                    mobile = COALESCE(NULLIF(%s, ''), mobile),
                    case_type = %s,
                    court_name = %s,
                    judge_name = %s,
                    opposite_party = %s,
                    hearing_date = %s,
                    next_hearing = %s,
                    status = %s,
                    drive_folder_id = %s,
                    drive_folder_link = %s,
                    ad_sync_status = %s,
                    ad_created_at = %s,
                    ad_sync_message = %s
                WHERE id = %s
            """, (
                ad_case_id,
                ad_client_id,
                client_local_id,
                case_number,
                case_number,
                case_title,
                client_name,
                client_mobile,
                case_type,
                court_name,
                judge_name,
                opposite_party,
                next_hearing,
                next_hearing,
                status,
                folder_id,
                folder_link,
                "MIRRORED",
                ad_created_at,
                "Mirrored from Advocate Diaries by /synccases",
                existing_id
            ))

            updated += 1

        else:
            cur.execute("""
                INSERT INTO cases
                (
                    ad_case_id,
                    ad_client_id,
                    client_id,
                    case_id,
                    case_number,
                    case_title,
                    client_name,
                    mobile,
                    case_type,
                    court_name,
                    opposite_party,
                    hearing_date,
                    next_hearing,
                    fee_agreed,
                    advance_received,
                    status,
                    judge_name,
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
                    %s, %s, %s, %s, %s,
                    %s, %s
                )
            """, (
                ad_case_id,
                ad_client_id,
                client_local_id,
                case_number,
                case_number,
                case_title,
                client_name,
                client_mobile,
                case_type,
                court_name,
                opposite_party,
                next_hearing,
                next_hearing,
                "",
                "",
                status,
                judge_name,
                folder_id,
                folder_link,
                "MIRRORED",
                ad_created_at,
                "Mirrored from Advocate Diaries by /synccases"
            ))

            added += 1

    conn.commit()

    cur.execute("""
        INSERT INTO sync_logs
        (
            sync_type,
            total_fetched,
            added_count,
            updated_count,
            folders_created,
            folders_reused,
            skipped_count,
            status,
            message
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        "advocate_diaries_cases",
        len(all_cases),
        added,
        updated,
        folders_created,
        folders_reused,
        skipped,
        "SUCCESS",
        "Advocate Diaries case sync completed"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return {
        "total": len(all_cases),
        "added": added,
        "updated": updated,
        "folders_created": folders_created,
        "folders_reused": folders_reused,
        "skipped": skipped
    }


async def synccases(update, context):
    await update.message.reply_text(
        "⏳ Syncing Advocate Diaries cases in background..."
    )

    try:
        result = await asyncio.to_thread(
            run_synccases_blocking
        )

        await update.message.reply_text(
            f"✅ Advocate Diaries sync completed.\n\n"
            f"📥 Total fetched: {result['total']}\n"
            f"➕ Added locally: {result['added']}\n"
            f"🔄 Updated locally: {result['updated']}\n"
            f"📁 Drive folders created/found: {result['folders_created']}\n"
            f"♻️ Existing folders reused: {result['folders_reused']}\n"
            f"⏭ Cases skipped: {result['skipped']}"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Sync failed:\n"
            f"{type(e).__name__}: {e}"
        )    

async def daily_ad_case_sync_job(context):
    try:
        result = await asyncio.to_thread(
            run_synccases_blocking
        )

        print(
            "DAILY AD SYNC COMPLETED: "
            f"total={result['total']}, "
            f"added={result['added']}, "
            f"updated={result['updated']}, "
            f"folders_created={result['folders_created']}, "
            f"folders_reused={result['folders_reused']}, "
            f"skipped={result['skipped']}"
        )

    except Exception as e:
        print(
            f"DAILY AD SYNC FAILED: "
            f"{type(e).__name__}: {e}"
        )

async def syncreport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
        SELECT
            created_at,
            total_fetched,
            added_count,
            updated_count,
            folders_created,
            folders_reused,
            skipped_count,
            status,
            message
        FROM sync_logs
        WHERE sync_type = 'advocate_diaries_cases'
        ORDER BY created_at DESC
        LIMIT 1
    """)

    row = cur.fetchone()

    if not row:
        await update.message.reply_text(
            "No sync report found yet. Run /synccases first."
        )
        return

    await update.message.reply_text(
        f"📊 LATEST ADVOCATE DIARIES SYNC REPORT\n\n"
        f"🕒 Time: {row[0]}\n"
        f"📌 Status: {row[7]}\n"
        f"📥 Total fetched: {row[1]}\n"
        f"➕ Added: {row[2]}\n"
        f"🔄 Updated: {row[3]}\n"
        f"📁 Folders created/found: {row[4]}\n"
        f"♻️ Folders reused: {row[5]}\n"
        f"⏭ Skipped: {row[6]}\n"
        f"📝 Message: {row[8]}"
    )

async def case(update, context):
    if len(context.args) == 0:
        await update.message.reply_text("Use /case CASE_NUMBER")
        return

    case_number = context.args[0]

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    r = requests.get(
        f"{AD_API}/court_cases?search={case_number}",
        headers=headers
    )

    if r.status_code == 200:
        data = r.json()["data"]

        if len(data) == 0:
            await update.message.reply_text("Case not found")
            return

        c = data[0]

        msg = (
            f"Case No: {c.get('case_number')}\n"
            f"Client: {c.get('client_name')}\n"
            f"Court: {c.get('court_name')}\n"
            f"Status: {c.get('status')}\n"
            f"Next Hearing: {c.get('hearing_date')}"
        )

        await update.message.reply_text(msg)

    else:
        await update.message.reply_text(
            f"Fetch failed\nStatus: {r.status_code}"
        )
async def pendingcases(update, context):
    await update.message.reply_text("Fetching pending cases...")

    ad_login()

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    all_cases = []
    page = 1

    while True:
        r = requests.get(
            f"{AD_API}/court_cases?page={page}",
            headers=headers
        )

        if r.status_code == 404:
            break

        if r.status_code != 200:
            await update.message.reply_text(
                f"Failed at page {page}\nStatus: {r.status_code}"
            )
            return

        data = r.json()["data"]

        if not data:
            break

        all_cases.extend(data)
        page += 1

    pending = []

    for c in all_cases:
        if str(c.get("status", "")).lower() == "pending":
            pending.append(c)

    if not pending:
        await update.message.reply_text("No pending cases.")
        return

    msg = "\n".join(
        [f"{c['case_number']} | {c['client_name']}" for c in pending]
    )

    chunk_size = 3500

    for i in range(0, len(msg), chunk_size):
        await update.message.reply_text(msg[i:i+chunk_size])
async def searchcase(update, context):
    if len(context.args) == 0:
        await update.message.reply_text("Use /searchcase NAME")
        return

    keyword = " ".join(context.args)

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    all_results = []
    page = 1

    while True:
        r = requests.get(
            f"{AD_API}/court_cases?page={page}&search={keyword}",
            headers=headers
        )

        if r.status_code != 200:
            break

        data = r.json()["data"]

        if not data:
            break

        all_results.extend(data)
        page += 1

    if not all_results:
        await update.message.reply_text("No results found")
        return

    msg = "\n".join(
        [f"{c['case_number']} | {c['client_name']}" for c in all_results]
    )

    await update.message.reply_text(msg)    
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Cancelled"); return ConversationHandler.END
async def markattendance(update, context):
    if len(context.args) == 0:
        await update.message.reply_text(
            "Use: /markattendance STAFFNAME"
        )
        return

    staff = context.args[0]

    cur.execute(
        "UPDATE staff SET attendance='Present' WHERE name=%s",
        (staff,)
    )
    conn.commit()

    await update.message.reply_text(
        f"{staff} marked present."
    )


async def assignresponsibility(update, context):
    case_number = context.args[0]
    staff = context.args[1]
    work = " ".join(context.args[2:])

    cur.execute(
        "INSERT INTO case_responsibility (case_number, staff_name, responsibility) VALUES (%s,%s,%s)",
        (case_number, staff, work)
    )
    conn.commit()

    await update.message.reply_text("Responsibility assigned.")

async def attendanceapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app_url = os.getenv("ATTENDANCE_APP_URL")

    if not app_url:
        await update.effective_message.reply_text(
            "❌ ATTENDANCE_APP_URL is not set."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📍 Open Attendance App",
                web_app=WebAppInfo(url=app_url)
            )
        ]
    ]

    await update.effective_message.reply_text(
        "📍 Open the Attendance App to mark check-in or check-out.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
async def task_button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    try:
        action, task_id = query.data.split(":", 1)
    except ValueError:
        await query.message.reply_text(
            "❌ Invalid task button."
        )
        return

    context.args = [task_id]

    if action == "taskdetails":
        await taskdetails(
            update,
            context
        )

    elif action == "completetask":
        await show_task_completion_confirmation(
            update,
            context,
            task_id
        )

    elif action == "confirmcomplete":
        await completetask(
            update,
            context
        )

    elif action == "cancelcomplete":
        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(
            f"❌ Task #{task_id} completion cancelled."
        )

    else:
        await query.message.reply_text(
            "❌ Unknown task action."
        )

async def show_task_completion_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    task_id: str
):
    query = update.callback_query

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                task,
                case_number,
                notes,
                assigned_to,
                status
            FROM tasks
            WHERE id = %s
            LIMIT 1
        """, (
            task_id,
        ))

        row = cur.fetchone()

    finally:
        cur.close()
        conn.close()

    if not row:
        await query.message.reply_text(
            f"❌ Task #{task_id} not found."
        )
        return

    (
        task_text,
        case_number,
        case_title,
        assigned_to,
        status
    ) = row

    if str(status).upper() == "COMPLETED":
        await query.message.reply_text(
            f"ℹ️ Task #{task_id} is already completed."
        )
        return

    message = (
        f"⚠️ CONFIRM TASK COMPLETION\n\n"
        f"🆔 Task #{task_id}\n"
        f"👤 Assigned To: {assigned_to}\n"
    )

    if case_title:
        message += (
            f"⚖️ {case_title}\n"
        )

    if case_number:
        message += (
            f"🔢 {case_number}\n"
        )

    message += (
        f"📝 {task_text}\n\n"
        f"Mark this task as completed?"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Yes, Complete",
                callback_data=f"confirmcomplete:{task_id}"
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"cancelcomplete:{task_id}"
            )
        ]
    ]

    await query.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )
    
async def mychatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Chat ID: {update.effective_chat.id}\n"
        f"Your User ID: {update.effective_user.id}"
    )

try:
    backfill_clients_from_cases(cur)
    conn.commit()
except Exception as exc:
    conn.rollback()
    print(
        "CLIENT MIRROR BACKFILL FAILED: "
        f"{type(exc).__name__}: {exc}"
    )

app = ApplicationBuilder().token(TOKEN).build()

# Register modular /start, /newcase and /findcase before legacy handlers.
register_case_handlers(app)

communication_handler = (
    build_communication_conversation_handler()
)

app.add_handler(
    communication_handler
)

app.add_handler(
    CallbackQueryHandler(
        communication_callback,
        pattern=r"^comm:"
    )
)

app.add_handler(
    CommandHandler(
        "missingmobiles",
        missingmobiles
    )
)

app.add_handler(
    CommandHandler(
        "pendingclientverification",
        pendingclientverification
    )
)

app.add_handler(
    CommandHandler(
        "confirmclientdetails",
        confirmclientdetails
    )
)

app.add_handler(
    CommandHandler(
        "clientchanges",
        clientchanges
    )
)

app.add_handler(
    CommandHandler(
        "messagehistory",
        messagehistory
    )
)

app.add_handler(
    CommandHandler(
        "refreshofficeprofile",
        refreshofficeprofile
    )
)

upload_handler = ConversationHandler(
    entry_points=[
        CommandHandler(
            "upload",
            upload_start
        ),
        CallbackQueryHandler(
            upload_category_callback,
            pattern=r"^docupload:"
        )
    ],

    states={
        WAITING_FILE: [
            MessageHandler(
                filters.Document.ALL
                | filters.PHOTO,
                upload_file
            )
        ],

        CONFIRM_DUPLICATE_UPLOAD: [
            CallbackQueryHandler(
                duplicate_upload_callback,
                pattern=(
                    r"^duplicate_upload:"
                )
            )
        ]
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel_upload
        )
    ],

    allow_reentry=True
)
conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("newcase", newcase)
    ],

    states={
        CLIENT: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                client
            )
        ],

        MOBILE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                mobile
            )
        ],

        ADVOCATEFOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, advocate_for)],
        CLIENTTYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, client_type_input)],
        TITLEPETITIONER: [MessageHandler(filters.TEXT & ~filters.COMMAND, title_petitioner)],
        TITLERESPONDENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, title_respondent)],
        
        CASETYPE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                case_type
            )
        ],

        COURT: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                court
            )
        ],

        JUDGE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                judge
            )
        ],
        
        OPPOSITE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                opposite
            )
        ],

        HEARING: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                hearing
            )
        ],

        FEE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                fee
            )
        ],

        ADVANCE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                advance
            )
        ],

        CONFIRM: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                confirm_newcase
            )
        ]
    },

    fallbacks=[
        CommandHandler("cancel", cancel),
        CommandHandler("findcase", findcase)
    ]
)

app.add_handler(conv_handler)
app.add_handler(CommandHandler("testad", test_ad))
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("findcase", findcase))
app.add_handler(CommandHandler("synccases", synccases))
app.add_handler(CommandHandler("case", case))
app.add_handler(CommandHandler("pendingcases", pendingcases))
app.add_handler(CommandHandler("todayhearings", todayhearings))
app.add_handler(CommandHandler("tomorrowcause", tomorrowcause))
app.add_handler(
    CommandHandler("explore", explore)
)
app.add_handler(
    CommandHandler("attendance", attendance)
)
app.add_handler(
    CommandHandler("approveattendance", approve_attendance)
)
app.add_handler(CommandHandler("linkstaff", linkstaff))
app.add_handler(CommandHandler("checkin", checkin))
app.add_handler(CommandHandler("checkout", checkout))
app.add_handler(CommandHandler("testweb", test_web))
app.add_handler(CommandHandler("pendingfees", pendingfees))
app.add_handler(CommandHandler("searchcase", searchcase))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("addpayment", addpayment))
app.add_handler(CommandHandler("closecase", closecase))
app.add_handler(CommandHandler("addnote", addnote))
app.add_handler(CommandHandler("pendingtasks", pendingtasks))
app.add_handler(CommandHandler("assignresponsibility", assignresponsibility))
app.add_handler(CommandHandler("mychatid", mychatid))
app.add_handler(
    CommandHandler("testcausejob", test_cause_job)
)
app.add_handler(CommandHandler("works", works))
app.add_handler(CommandHandler("work", work))
app.add_handler(CommandHandler("completework", completework))
app.add_handler(CommandHandler("assignwork", assignwork))
app.add_handler(CommandHandler("mytasks", mytasks))
app.add_handler(CommandHandler("stafftasks", stafftasks))
app.add_handler(CommandHandler("completetask", completetask))
app.add_handler(CommandHandler("testpendingsummary", test_pending_summary))
app.add_handler(
    CommandHandler("assigntask", assigntask)
)
app.add_handler(
    CommandHandler("teststafflogin", teststafflogin)
)
app.add_handler(
    CommandHandler(
        "testcompletedsummary",
        test_completed_summary
    )
)
app.add_handler(CommandHandler("testdeadlinealert", test_deadline_alert))
app.add_handler(
    CommandHandler(
        "testmanualdeadline",
        test_manual_deadline_reminder
    )
)
app.add_handler(CommandHandler("commands", commands))

app.add_handler(upload_handler)
app.add_handler(CommandHandler("casefolder", casefolder))
app.add_handler(CommandHandler("casefiles", casefiles))
app.add_handler(CommandHandler("files", files))
app.add_handler(CommandHandler("latestfiles", latestfiles))
app.add_handler(CommandHandler("sharecasefolder", sharecasefolder))
app.add_handler(CommandHandler("filehistory", filehistory))

app.add_handler(
    CommandHandler(
        "openfile",
        openfile
    )
)

app.add_handler(
    CommandHandler(
        "findfile",
        findfile
    )
)

app.add_handler(
    CommandHandler(
        "testadwebcreatecase",
        test_ad_web_create_case
    )
)

app.add_handler(
    CommandHandler(
        "testadrealcase",
        test_ad_web_create_real_case
    )
)

app.add_handler(CommandHandler("debugcasejson", debugcasejson))
app.add_handler(CommandHandler("syncreport", syncreport))
app.add_handler(CommandHandler("attendanceapp", attendanceapp))
app.add_handler(
    CommandHandler(
        "syncattendancetoday",
        sync_today_attendance_sessions
    )
)
app.add_handler(
    CommandHandler(
        "whoinoffice",
        whoinoffice
    )
)

app.add_handler(
    CommandHandler(
        "attendancetoday",
        attendancetoday
    )
)

app.add_handler(
    CommandHandler(
        "staffattendance",
        staffattendance
    )
)

app.add_handler(
    CommandHandler(
        "testforgotcheckout",
        test_forgot_checkout
    )
)

app.add_handler(
    CommandHandler(
        "testattendancesummary",
        test_attendance_summary
    )
)
app.add_handler(
    CommandHandler(
        "taskdetails",
        taskdetails
    )
)

app.add_handler(
    CommandHandler(
        "taskhistory",
        taskhistory
    )
)

app.add_handler(
    CommandHandler(
        "morningdashboard",
        morningdashboard
    )
)

app.add_handler(
    CommandHandler(
        "teststaffbriefs",
        test_staff_morning_briefs
    )
)

app.add_handler(
    CallbackQueryHandler(
        task_button_callback,
        pattern=(
            r"^(taskdetails|completetask|"
            r"confirmcomplete|cancelcomplete):\d+$"
        )
    )
)

app.add_handler(
    CommandHandler(
        "reassigntask",
        reassign_task
    )
)

app.add_handler(
    CommandHandler(
        "reassignhistory",
        reassign_history
    )
)

app.add_handler(
    CommandHandler(
        "reopentask",
        reopen_task
    )
)

app.add_handler(
    CommandHandler(
        "reopenhistory",
        reopen_history
    )
)
app.add_handler(
    CommandHandler(
        "setpriority",
        set_task_priority
    )
)



app.add_handler(
    CommandHandler(
        "casecolumns",
        show_case_columns
    )
)

app.add_handler(
    CommandHandler(
        "clienttimeline",
        clienttimeline
    )
)

app.add_handler(
    CommandHandler(
        "synctimeline",
        synctimeline
    )
)

app.add_handler(
    CommandHandler(
        "addtimeline",
        addtimeline
    )
)

app.add_handler(
    CommandHandler(
        "generatehearingreminders",
        generatehearingreminders
    )
)

app.add_handler(
    CommandHandler(
        "hearingqueue",
        hearingqueue
    )
)

app.add_handler(
    CommandHandler(
        "hearingpreview",
        hearingpreview
    )
)

app.add_handler(
    CallbackQueryHandler(
        hearing_automation_callback,
        pattern=r"^hear:"
    )
)

app.add_handler(
    CommandHandler(
        "synccasesv2",
        synccasesv2
    )
)

app.add_handler(
    CommandHandler(
        "inspectadcase",
        inspectadcase
    )
)

app.add_handler(
    CommandHandler(
        "inspectadclient",
        inspectadclient
    )
)

app.add_handler(
    CommandHandler(
        "synccasesv3",
        synccasesv3
    )
)

app.add_handler(
    CommandHandler(
        "missingmobilesreport",
        missingmobilesreport
    )
)

app.add_handler(
    CommandHandler(
        "repairmobiles",
        repairmobiles
    )
)

app.add_handler(
    CommandHandler(
        "mobileaudit",
        mobileaudit
    )
)

app.add_handler(
    CommandHandler(
        "mobileupdatequeue",
        mobileupdatequeue
    )
)

app.add_handler(
    CommandHandler(
        "mobileupdatequeuesummary",
        mobileupdatequeuesummary
    )
)

app.job_queue.run_repeating(
    monitor_attendance_job,
    interval=300,
    first=30
)


app.job_queue.run_daily(
    pending_tasks_summary_job,
    time=time(hour=17, minute=15, tzinfo=ZoneInfo("Asia/Kolkata")),
    name="pending_tasks_515pm"
)

app.job_queue.run_daily(
    completed_yesterday_summary_job,
    time=time(
        hour=9,
        minute=15,
        tzinfo=ZoneInfo("Asia/Kolkata")
    ),
    name="completed_yesterday_915am"
)

app.job_queue.run_daily(
    task_deadline_alert_job,
    time=time(
        hour=9,
        minute=30,
        tzinfo=ZoneInfo("Asia/Kolkata")
    ),
    name="task_deadline_alert_930am"
)

app.job_queue.run_daily(
    manual_deadline_5pm_reminder_job,
    time=time(
        hour=17,
        minute=0,
        tzinfo=ZoneInfo("Asia/Kolkata")
    ),
    name="manual_deadline_5pm"
)


app.job_queue.run_daily(
    daily_ad_case_sync_job,
    time=time(
        hour=8,
        minute=45,
        tzinfo=ZoneInfo("Asia/Kolkata")
    ),
    name="daily_ad_case_sync_845am"
)

app.job_queue.run_daily(
    morning_dashboard_job,
    time=time(
        hour=9,
        minute=5,
        tzinfo=ZoneInfo(
            "Asia/Kolkata"
        )
    ),
    name="morning_dashboard_905am"
)

app.job_queue.run_daily(
    staff_morning_brief_job,
    time=time(
        hour=9,
        minute=10,
        tzinfo=ZoneInfo(
            "Asia/Kolkata"
        )
    ),
    name="staff_morning_briefs_910am"
)


app.job_queue.run_daily(
    forgot_checkout_job,
    time=time(
        hour=20,
        minute=30,
        tzinfo=ZoneInfo(
            "Asia/Kolkata"
        )
    ),
    name="forgot_checkout_reminder"
)

app.job_queue.run_daily(
    daily_attendance_summary_job,
    time=time(
        hour=21,
        minute=0,
        tzinfo=ZoneInfo(
            "Asia/Kolkata"
        )
    ),
    name="daily_attendance_summary"
)


app.job_queue.run_daily(
    hearing_reminder_generation_job,
    time=time(
        hour=8,
        minute=30,
        tzinfo=ZoneInfo(
            "Asia/Kolkata"
        )
    ),
    name="hearing_reminder_generation"
)


threading.Thread(
    target=run_attendance_app,
    daemon=True
).start()

ensure_client_timeline_table()
ensure_activity_schema()
ensure_hearing_automation_tables()
ad_login()
print("Bot started")
app.run_polling()
