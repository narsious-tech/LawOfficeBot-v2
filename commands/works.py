from telegram import Update
from telegram.ext import ContextTypes
from bs4 import BeautifulSoup
import psycopg2
from datetime import datetime
from config import DATABASE_URL
from advocate_web import AdvocateWeb
from services.activity_logger import (
    log_activity,
    log_activity_with_cursor,
)

web = AdvocateWeb()

PRIORITY_ICONS = {
    "URGENT": "🔴",
    "HIGH": "🟠",
    "NORMAL": "🔵",
    "LOW": "⚪"
}


def normalize_priority(priority):
    value = (priority or "NORMAL").strip().upper()

    if value not in PRIORITY_ICONS:
        return "NORMAL"

    return value


def priority_icon(priority):
    return PRIORITY_ICONS[
        normalize_priority(priority)
    ]


async def send_long_reply(update, message):
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

        await update.effective_message.reply_text(
            chunk
        )

async def works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "pending"

    if context.args:
        status = context.args[0].lower()

    response = web.works(status)

    if response.status_code != 200:
        await update.effective_message.reply_text(
            f"❌ Unable to fetch works. Status: {response.status_code}"
        )
        return

    soup = BeautifulSoup(response.text, "lxml")
    tbody = soup.find("tbody")

    if tbody is None:
        await update.effective_message.reply_text(
            f"📋 Works - {status.title()}\n\nNo works found."
        )
        return

    rows = tbody.find_all("tr")
    context.user_data["works_map"] = {}

    message = f"📋 WORKS - {status.upper()}\n\n"

    for i, row in enumerate(rows, start=1):
        cols = row.find_all("td")

        if len(cols) < 3:
            continue

        client = cols[0].get_text(" ", strip=True)
        case_details = cols[1].get_text("\n", strip=True)
        description = cols[2].get_text(" ", strip=True)

        case_lines = case_details.split("\n")

        case_title = ""
        case_type = ""
        case_number = ""
        next_hearing = ""

        for idx, line in enumerate(case_lines):
            line = line.strip()

            if line.startswith("Case Title:"):
                case_title = line.replace("Case Title:", "").strip()

            elif line.startswith("Case Type:"):
                case_type = line.replace("Case Type:", "").strip()

            elif line.startswith("Case Number:"):
                case_number = line.replace("Case Number:", "").strip()

            elif line.startswith("Next Hearing:"):
                if idx + 1 < len(case_lines):
                    next_hearing = case_lines[idx + 1].strip()
        complete_link = row.find(
            "a",
            href=lambda href: href and "mark_as_complete" in href
        )

        work_id = None

        if complete_link:
            href = complete_link.get("href", "")

            if "work=" in href:
                work_id = href.split("work=")[1].split("&")[0]

        short_case = case_details.split("\n")[0]

        full_item = (
            f"📋 Work #{i}\n\n"
            f"👤 Client: {client}\n\n"
            f"⚖️ {case_details}\n\n"
            f"📝 Work: {description}"
        )

        context.user_data["works_map"][str(i)] = {
            "details": full_item,
            "work_id": work_id,
            "client": client,
            "case_title": case_title,
            "case_type": case_type,
            "case_number": case_number,
            "next_hearing": next_hearing,
            "work_description": description
        }
        message += (
            f"{i}. 👤 {client}\n"
            f"⚖️ {short_case}\n"
            f"📝 {description}\n\n"
        )

    message += (
        "For full details: /work number\n"
        "To complete: /completework number"
    )

    if len(message) > 3900:
        message = message[:3850] + "\n\n⚠️ List truncated. Use /work number."

    await update.effective_message.reply_text(message)


async def work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /work number")
        return

    key = context.args[0]
    works_map = context.user_data.get("works_map", {})

    item = works_map.get(key)

    if not item:
        await update.effective_message.reply_text(
            "Invalid number. First run /works, then use /work number."
        )
        return

    await update.effective_message.reply_text(item["details"])


async def completework(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /completework number\nExample: /completework 1"
        )
        return

    number = context.args[0]
    works_map = context.user_data.get("works_map", {})
    item = works_map.get(number)

    if not item:
        await update.effective_message.reply_text(
            "❌ Invalid work number.\nFirst run /works, then use /completework number."
        )
        return

    work_id = item.get("work_id")

    if not work_id:
        await update.effective_message.reply_text(
            "❌ Completion ID could not be found for this work."
        )
        return

    response = web.complete_work(work_id)

    if response.status_code == 200:
        log_activity(
            case_value=item.get("case_number", ""),
            event_code="AD_WORK_COMPLETED",
            details=(
                f"Work: {item.get('work_description') or '-'}\n"
                f"Advocate Diaries Work ID: {work_id}"
            ),
            source_module="ADVOCATE_DIARIES_WORK",
            source_id=str(work_id),
            user_id=update.effective_user.id,
            metadata={
                "work_id": str(work_id),
                "work_description": item.get(
                    "work_description",
                    ""
                ),
            }
        )

        await update.effective_message.reply_text(
            f"✅ Work #{number} marked as completed.\n\n{item['details']}"
        )
        works_map.pop(number, None)
    else:
        await update.effective_message.reply_text(
            f"❌ Unable to complete work. Status: {response.status_code}"
        )

async def assignwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/assignwork STAFF_NAME WORK_NUMBER WORK_NUMBER...\n\n"
            "Example:\n"
            "/assignwork Happy 1 3 7"
        )
        return

    staff_name = context.args[0]
    work_numbers = context.args[1:]

    works_map = context.user_data.get("works_map", {})

    if not works_map:
        await update.effective_message.reply_text(
            "❌ Works list not loaded.\n\n"
            "First run /works and then assign the required Work numbers."
        )
        return

    # Remove duplicate numbers while preserving order
    work_numbers = list(dict.fromkeys(work_numbers))

    valid_items = []
    invalid_numbers = []

    for number in work_numbers:
        item = works_map.get(number)

        if item:
            valid_items.append((number, item))
        else:
            invalid_numbers.append(number)

    if invalid_numbers:
        await update.effective_message.reply_text(
            "❌ Invalid Work number(s): "
            + ", ".join(invalid_numbers)
            + "\n\nNo tasks were assigned."
        )
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Verify staff member exists
        cur.execute("""
            SELECT telegram_user_id, staff_name
            FROM staff_accounts
            WHERE LOWER(staff_name) = LOWER(%s)
            AND is_active = TRUE
        """, (staff_name,))

        staff_row = cur.fetchone()

        if staff_row:
            telegram_user_id = staff_row[0]
            staff_name = staff_row[1]
        else:
            # Staff may exist but may not yet have linked Telegram
            cur.execute("""
                SELECT name
                FROM staff
                WHERE LOWER(name) = LOWER(%s)
            """, (staff_name,))

            staff_master = cur.fetchone()

            if not staff_master:
                await update.effective_message.reply_text(
                    f"❌ Staff member '{staff_name}' not found."
                )
                return

            staff_name = staff_master[0]
            telegram_user_id = None

        assigned_items = []

        for number, item in valid_items:
            work_id = item.get("work_id")

            # Prevent duplicate active assignment of same AD Work
            cur.execute("""
                SELECT id
                FROM tasks
                WHERE source_type = 'advocate_diaries_work'
                AND source_work_id = %s
                AND LOWER(assigned_to) = LOWER(%s)
                AND UPPER(status) = 'PENDING'
            """, (
                str(work_id) if work_id is not None else None,
                staff_name
            ))

            existing = cur.fetchone()

            if existing:
                continue

            cur.execute("""
                INSERT INTO tasks
                (
                    case_number,
                    assigned_to,
                    task,
                    deadline,
                    due_at,
                    status,
                    source_type,
                    source_work_id,
                    assigned_by,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                item.get("case_number", ""),
                staff_name,
                item.get("work_description", ""),
                item.get("next_hearing") or None,
                None,
                "PENDING",
                "advocate_diaries_work",
                str(work_id) if work_id is not None else None,
                update.effective_user.id,
                item.get("case_title", "")
            ))
            task_id = cur.fetchone()[0]

            log_activity_with_cursor(
                cur,
                case_value=item.get(
                    "case_number",
                    ""
                ),
                event_code="AD_WORK_ASSIGNED",
                details=(
                    f"Task #{task_id}\n"
                    f"Assigned to: {staff_name}\n"
                    f"Work: "
                    f"{item.get('work_description') or '-'}\n"
                    f"Next Hearing: "
                    f"{item.get('next_hearing') or '-'}"
                ),
                source_module="TASK",
                source_id=str(task_id),
                user_id=update.effective_user.id,
                metadata={
                    "task_id": task_id,
                    "work_id": (
                        str(work_id)
                        if work_id is not None
                        else None
                    ),
                    "assigned_to": staff_name,
                    "source_type": (
                        "advocate_diaries_work"
                    ),
                }
            )

            assigned_items.append({
                "task_id": task_id,
                "work_number": number,
                "case_title": item.get("case_title", ""),
                "case_number": item.get("case_number", ""),
                "task": item.get("work_description", ""),
                "deadline": item.get("next_hearing", "")
            })

        conn.commit()

    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Assignment failed:\n{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not assigned_items:
        await update.effective_message.reply_text(
            f"ℹ️ No new tasks assigned to {staff_name}.\n"
            "The selected Works may already be assigned and pending."
        )
        return

    confirmation = (
        f"✅ {len(assigned_items)} task(s) assigned to {staff_name}.\n\n"
    )

    for item in assigned_items:
        confirmation += (
            f"🆔 Task #{item['task_id']}\n"
            f"⚖️ {item['case_title']}\n"
            f"🔢 {item['case_number']}\n"
            f"📝 {item['task']}\n"
        )

        if item["deadline"]:
            confirmation += (
                f"📅 Deadline / Next Hearing: {item['deadline']}\n"
            )

        confirmation += "\n"

    await update.effective_message.reply_text(
        confirmation[:3900]
    )

    if telegram_user_id:
        private_message = (
            f"📌 NEW TASK ASSIGNMENTS\n\n"
            f"You have been assigned {len(assigned_items)} new task(s).\n\n"
        )

        for item in assigned_items:
            private_message += (
                f"🆔 Task #{item['task_id']}\n"
                f"⚖️ {item['case_title']}\n"
                f"🔢 {item['case_number']}\n"
                f"📝 {item['task']}\n"
            )

            if item["deadline"]:
                private_message += (
                    f"📅 Deadline / Next Hearing: "
                    f"{item['deadline']}\n"
                )

            private_message += "\n"

        private_message += (
            "Use /mytasks to view all pending tasks.\n"
            "Use /completetask TASK_ID after completion."
        )

        try:
            await context.bot.send_message(
                chat_id=telegram_user_id,
                text=private_message[:3900]
            )

        except Exception as e:
            await update.effective_message.reply_text(
                f"⚠️ Tasks were saved successfully, but private "
                f"notification to {staff_name} failed:\n"
                f"{type(e).__name__}: {e}"
            )

async def assigntask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/assigntask STAFF TASK\n\n"
            "Optional deadline:\n"
            "/assigntask STAFF TASK | DD-MM-YYYY HH:MM AM/PM\n\n"
            "Example:\n"
            "/assigntask Happy Komal Sharma both suits ko scan karo "
            "with judgements | 08-07-2026 6:00 PM"
        )
        return

    staff_name_input = context.args[0]

    full_text = " ".join(context.args[1:]).strip()

    task_text = full_text
    deadline = None

    # Parse optional deadline after |
    if "|" in full_text:
        task_text, deadline_text = full_text.rsplit("|", 1)

        task_text = task_text.strip()
        deadline_text = deadline_text.strip()

        try:
            deadline = datetime.strptime(
                deadline_text,
                "%d-%m-%Y %I:%M %p"
            )

        except ValueError:
            await update.effective_message.reply_text(
                "❌ Invalid deadline format.\n\n"
                "Use:\n"
                "DD-MM-YYYY HH:MM AM/PM\n\n"
                "Example:\n"
                "08-07-2026 6:00 PM"
            )
            return

    if not task_text:
        await update.effective_message.reply_text(
            "❌ Task description cannot be empty."
        )
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT telegram_user_id, staff_name
            FROM staff_accounts
            WHERE LOWER(staff_name) = LOWER(%s)
            AND is_active = TRUE
        """, (staff_name_input,))

        staff_row = cur.fetchone()

        if not staff_row:
            await update.effective_message.reply_text(
                f"❌ Active linked staff member "
                f"'{staff_name_input}' not found."
            )
            return

        telegram_user_id = staff_row[0]
        staff_name = staff_row[1]

        cur.execute("""
            INSERT INTO tasks
            (
                case_number,
                assigned_to,
                task,
                deadline,
                due_at,
                status,
                source_type,
                source_work_id,
                assigned_by,
                notes
            )
            VALUES
            (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            RETURNING id
        """, (
            None,                       
            staff_name,                 
            task_text,                  
            None,                       
            deadline,                   
            "PENDING",                  
            "manual",                   
            None,                       
            update.effective_user.id,   
            None                        
        ))

        task_id = cur.fetchone()[0]

        conn.commit()
    
    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Task assignment failed:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    deadline_line = ""

    if deadline:
        deadline_line = (
            "\n⏰ Deadline: "
            + deadline.strftime("%d-%m-%Y %I:%M %p")
        )

    await update.effective_message.reply_text(
        f"✅ Task #{task_id} assigned to {staff_name}.\n\n"
        f"📝 {task_text}"
        f"{deadline_line}\n"
        f"📌 Status: PENDING"
    )

    try:
        await context.bot.send_message(
            chat_id=telegram_user_id,
            text=(
                "📌 NEW TASK ASSIGNED\n\n"
                f"🆔 Task #{task_id}\n"
                f"📝 {task_text}"
                f"{deadline_line}\n"
                f"📌 Status: PENDING\n\n"
                "Use /mytasks to view your pending tasks.\n"
                f"Use /completetask {task_id} after completion."
            )
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"⚠️ Task #{task_id} was saved successfully, "
            f"but private notification to {staff_name} failed:\n"
            f"{type(e).__name__}: {e}"
        )

async def mytasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_user_id = update.effective_user.id

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT staff_name
            FROM staff_accounts
            WHERE telegram_user_id = %s
              AND is_active = TRUE
            LIMIT 1
        """, (
            telegram_user_id,
        ))

        row = cur.fetchone()

        if not row:
            await update.effective_message.reply_text(
                "❌ Your staff account is not linked.\n"
                "Use /linkstaff first."
            )
            return

        staff_name = row[0]

        cur.execute("""
            SELECT
                id,
                case_number,
                task,
                deadline,
                due_at,
                status,
                notes,
                source_type,
                COALESCE(priority, 'NORMAL') AS priority
            FROM tasks
            WHERE LOWER(TRIM(assigned_to))
                  = LOWER(TRIM(%s))
              AND UPPER(status) = 'PENDING'

            ORDER BY
                CASE UPPER(
                    COALESCE(priority, 'NORMAL')
                )
                    WHEN 'URGENT' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'NORMAL' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,

                COALESCE(
                    due_at,

                    CASE
                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD HH24:MI:SS'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{2}-\\d{2}-\\d{4}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'DD-MM-YYYY'
                        )

                        ELSE NULL
                    END
                ) ASC NULLS LAST,

                created_at ASC NULLS LAST,
                id ASC
        """, (
            staff_name,
        ))

        rows = cur.fetchall()

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Unable to load your tasks:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"✅ No pending tasks for {staff_name}."
        )
        return

    priority_counts = {
        "URGENT": 0,
        "HIGH": 0,
        "NORMAL": 0,
        "LOW": 0
    }

    for row in rows:
        priority_value = normalize_priority(
            row[8]
        )

        priority_counts[priority_value] += 1

    message = (
        f"📋 MY PENDING TASKS — "
        f"{staff_name.upper()}\n\n"
        f"📌 Total Pending: {len(rows)}\n"
        f"🔴 Urgent: {priority_counts['URGENT']}\n"
        f"🟠 High: {priority_counts['HIGH']}\n"
        f"🔵 Normal: {priority_counts['NORMAL']}\n"
        f"⚪ Low: {priority_counts['LOW']}\n\n"
    )

    for (
        task_id,
        case_number,
        task_text,
        hearing_date,
        due_at,
        status,
        case_title,
        source_type,
        priority
    ) in rows:

        priority_value = normalize_priority(
            priority
        )

        icon = priority_icon(
            priority_value
        )

        message += (
            f"{icon} {priority_value} — "
            f"Task #{task_id}\n"
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
            f"📝 {task_text}\n"
        )

        if hearing_date:
            if (
                source_type
                == "advocate_diaries_work"
            ):
                message += (
                    f"📅 Next Hearing: "
                    f"{hearing_date}\n"
                )
            elif not due_at:
                message += (
                    f"⏰ Task Deadline: "
                    f"{hearing_date}\n"
                )

        if due_at:
            message += (
                f"⏰ Internal Deadline: "
                f"{due_at.strftime('%d-%m-%Y %I:%M %p')}\n"
            )

        message += (
            f"📌 Status: {status}\n"
            f"/taskdetails {task_id}\n"
            f"/completetask {task_id}\n\n"
            f"──────────────\n\n"
        )

    await send_long_reply(
        update,
        message
    )    
async def completetask(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /completetask TASK_ID\n"
            "Example: /completetask 4"
        )
        return

    task_id = context.args[0].strip()
    telegram_user_id = update.effective_user.id

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Confirm linked staff identity
        cur.execute("""
            SELECT staff_name
            FROM staff_accounts
            WHERE telegram_user_id = %s
              AND is_active = TRUE
        """, (telegram_user_id,))

        staff = cur.fetchone()

        if not staff:
            await update.effective_message.reply_text(
                "❌ Your staff account is not linked."
            )
            return

        staff_name = staff[0]

        # Read task before completing it
        cur.execute("""
            SELECT
                id,
                task,
                case_number,
                notes,
                source_type,
                source_work_id,
                status
            FROM tasks
            WHERE id = %s
              AND LOWER(assigned_to) = LOWER(%s)
            LIMIT 1
        """, (
            task_id,
            staff_name
        ))

        task_row = cur.fetchone()

        if not task_row:
            await update.effective_message.reply_text(
                "❌ Task not found or not assigned to you."
            )
            return

        (
            db_task_id,
            task_text,
            case_number,
            case_title,
            source_type,
            source_work_id,
            current_status
        ) = task_row

        if str(current_status).upper() == "COMPLETED":
            await update.effective_message.reply_text(
                f"ℹ️ Task #{db_task_id} is already completed."
            )
            return

        ad_result = None
        ad_success = False

        # Complete linked Advocate Diaries Work first
        if (
            source_type == "advocate_diaries_work"
            and source_work_id
        ):
            try:
                ad_response = web.complete_work(
                    str(source_work_id)
                )

                ad_result = (
                    f"Status {ad_response.status_code}"
                )

                if ad_response.status_code == 200:
                    ad_success = True

                else:
                    await update.effective_message.reply_text(
                        f"❌ Advocate Diaries Work completion failed.\n"
                        f"Status: {ad_response.status_code}\n\n"
                        f"Local Task #{db_task_id} was not completed."
                    )
                    return

            except Exception as e:
                await update.effective_message.reply_text(
                    f"❌ Advocate Diaries Work completion failed:\n"
                    f"{type(e).__name__}: {e}\n\n"
                    f"Local Task #{db_task_id} was not completed."
                )
                return

        # Mark local task completed
        cur.execute("""
            UPDATE tasks
            SET
                status = 'COMPLETED',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND LOWER(assigned_to) = LOWER(%s)
              AND UPPER(status) = 'PENDING'
            RETURNING id, task, completed_at
        """, (
            task_id,
            staff_name
        ))

        completed = cur.fetchone()

        if not completed:
            conn.rollback()

            await update.effective_message.reply_text(
                "❌ Task could not be completed locally."
            )
            return

        conn.commit()

        message = (
            f"✅ Task #{completed[0]} marked as completed.\n\n"
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
            f"📝 {completed[1]}\n"
        )

        if (
            source_type == "advocate_diaries_work"
            and source_work_id
        ):
            message += (
                f"\n✅ Advocate Diaries Work also completed.\n"
                f"🔗 Work ID: {source_work_id}"
            )

        else:
            message += (
                "\n📌 Manual/local task completed."
            )

        await update.effective_message.reply_text(
            message[:3900]
        )

    except Exception as e:
        conn.rollback()

        await update.effective_message.reply_text(
            f"❌ Task completion failed:\n"
            f"{type(e).__name__}: {e}"
        )

    finally:
        cur.close()
        conn.close()
async def pendingtasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                id,
                assigned_to,
                case_number,
                task,
                deadline,
                due_at,
                status,
                notes,
                source_type,
                COALESCE(priority, 'NORMAL') AS priority
            FROM tasks
            WHERE UPPER(status) = 'PENDING'

            ORDER BY
                LOWER(
                    COALESCE(
                        assigned_to,
                        'Unassigned'
                    )
                ) ASC,

                CASE UPPER(
                    COALESCE(priority, 'NORMAL')
                )
                    WHEN 'URGENT' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'NORMAL' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,

                COALESCE(
                    due_at,

                    CASE
                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD HH24:MI:SS'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{2}-\\d{2}-\\d{4}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'DD-MM-YYYY'
                        )

                        ELSE NULL
                    END
                ) ASC NULLS LAST,

                created_at ASC NULLS LAST,
                id ASC
        """)

        rows = cur.fetchall()

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Unable to load pending tasks:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "✅ No pending tasks."
        )
        return

    grouped = {}

    office_priority_counts = {
        "URGENT": 0,
        "HIGH": 0,
        "NORMAL": 0,
        "LOW": 0
    }

    for (
        task_id,
        assigned_to,
        case_number,
        task_text,
        hearing_date,
        due_at,
        status,
        case_title,
        source_type,
        priority
    ) in rows:

        staff = assigned_to or "Unassigned"

        priority_value = normalize_priority(
            priority
        )

        office_priority_counts[
            priority_value
        ] += 1

        if staff not in grouped:
            grouped[staff] = {
                "tasks": [],
                "priority_counts": {
                    "URGENT": 0,
                    "HIGH": 0,
                    "NORMAL": 0,
                    "LOW": 0
                }
            }

        grouped[staff][
            "priority_counts"
        ][priority_value] += 1

        grouped[staff]["tasks"].append({
            "id": task_id,
            "case_number": case_number,
            "task": task_text,
            "hearing_date": hearing_date,
            "due_at": due_at,
            "status": status,
            "case_title": case_title,
            "source_type": source_type,
            "priority": priority_value
        })

    message = (
        "📋 PENDING TASKS — STAFF WISE\n\n"
        f"📌 Total Pending: {len(rows)}\n"
        f"🔴 Urgent: "
        f"{office_priority_counts['URGENT']}\n"
        f"🟠 High: "
        f"{office_priority_counts['HIGH']}\n"
        f"🔵 Normal: "
        f"{office_priority_counts['NORMAL']}\n"
        f"⚪ Low: "
        f"{office_priority_counts['LOW']}\n\n"
    )

    for staff, data in grouped.items():
        tasks = data["tasks"]
        counts = data["priority_counts"]

        message += (
            f"👤 {staff.upper()}\n"
            f"📌 Open Tasks: {len(tasks)}\n"
            f"🔴 {counts['URGENT']}  "
            f"🟠 {counts['HIGH']}  "
            f"🔵 {counts['NORMAL']}  "
            f"⚪ {counts['LOW']}\n\n"
        )

        for item in tasks:
            icon = priority_icon(
                item["priority"]
            )

            message += (
                f"{icon} {item['priority']} — "
                f"Task #{item['id']}\n"
            )

            if item["case_title"]:
                message += (
                    f"⚖️ {item['case_title']}\n"
                )

            if item["case_number"]:
                message += (
                    f"🔢 {item['case_number']}\n"
                )

            message += (
                f"📝 {item['task']}\n"
            )

            if item["hearing_date"]:
                if (
                    item["source_type"]
                    == "advocate_diaries_work"
                ):
                    message += (
                        f"📅 Next Hearing: "
                        f"{item['hearing_date']}\n"
                    )

                elif not item["due_at"]:
                    message += (
                        f"⏰ Task Deadline: "
                        f"{item['hearing_date']}\n"
                    )

            if item["due_at"]:
                message += (
                    f"⏰ Internal Deadline: "
                    f"{item['due_at'].strftime('%d-%m-%Y %I:%M %p')}\n"
                )

            message += (
                f"📌 Status: "
                f"{item['status']}\n"
                f"/taskdetails {item['id']}\n\n"
            )

        message += (
            "──────────────\n\n"
        )

    await send_long_reply(
        update,
        message
    )
    
async def stafftasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/stafftasks STAFF_NAME\n\n"
            "Example:\n"
            "/stafftasks Happy"
        )
        return

    staff_name_input = " ".join(
        context.args
    ).strip()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                id,
                case_number,
                task,
                deadline,
                due_at,
                status,
                notes,
                source_type,
                assigned_to,
                COALESCE(priority, 'NORMAL') AS priority
            FROM tasks
            WHERE LOWER(TRIM(assigned_to))
                  = LOWER(TRIM(%s))
              AND UPPER(status) = 'PENDING'

            ORDER BY
                CASE UPPER(
                    COALESCE(priority, 'NORMAL')
                )
                    WHEN 'URGENT' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'NORMAL' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,

                COALESCE(
                    due_at,

                    CASE
                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD HH24:MI:SS'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'YYYY-MM-DD'
                        )

                        WHEN TRIM(
                            COALESCE(deadline, '')
                        ) ~
                        '^\\d{2}-\\d{2}-\\d{4}$'
                        THEN TO_TIMESTAMP(
                            TRIM(deadline),
                            'DD-MM-YYYY'
                        )

                        ELSE NULL
                    END
                ) ASC NULLS LAST,

                created_at ASC NULLS LAST,
                id ASC
        """, (
            staff_name_input,
        ))

        rows = cur.fetchall()

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Unable to load staff tasks:\n"
            f"{type(e).__name__}: {e}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            f"✅ No pending tasks for "
            f"{staff_name_input}."
        )
        return

    actual_staff_name = (
        rows[0][8]
        or staff_name_input
    )

    priority_counts = {
        "URGENT": 0,
        "HIGH": 0,
        "NORMAL": 0,
        "LOW": 0
    }

    for row in rows:
        priority_value = normalize_priority(
            row[9]
        )

        priority_counts[
            priority_value
        ] += 1

    message = (
        f"📋 PENDING TASKS — "
        f"{actual_staff_name.upper()}\n\n"
        f"📌 Total Pending: {len(rows)}\n"
        f"🔴 Urgent: {priority_counts['URGENT']}\n"
        f"🟠 High: {priority_counts['HIGH']}\n"
        f"🔵 Normal: {priority_counts['NORMAL']}\n"
        f"⚪ Low: {priority_counts['LOW']}\n\n"
    )

    for (
        task_id,
        case_number,
        task_text,
        hearing_date,
        due_at,
        status,
        case_title,
        source_type,
        assigned_to,
        priority
    ) in rows:

        priority_value = normalize_priority(
            priority
        )

        icon = priority_icon(
            priority_value
        )

        message += (
            f"{icon} {priority_value} — "
            f"Task #{task_id}\n"
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
            f"📝 {task_text}\n"
        )

        if hearing_date:
            if (
                source_type
                == "advocate_diaries_work"
            ):
                message += (
                    f"📅 Next Hearing: "
                    f"{hearing_date}\n"
                )

            elif not due_at:
                message += (
                    f"⏰ Task Deadline: "
                    f"{hearing_date}\n"
                )

        if due_at:
            message += (
                f"⏰ Internal Deadline: "
                f"{due_at.strftime('%d-%m-%Y %I:%M %p')}\n"
            )

        message += (
            f"📌 Status: {status}\n"
            f"/taskdetails {task_id}\n\n"
            f"──────────────\n\n"
        )

    await send_long_reply(
        update,
        message
    )