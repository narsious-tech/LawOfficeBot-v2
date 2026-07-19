import os
import re
import logging
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo

import psycopg2

from telegram.ext import ContextTypes

from config import DATABASE_URL
from advocate_web import AdvocateWeb
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

OFFICE_GROUP_CHAT_ID = os.getenv(
    "OFFICE_GROUP_CHAT_ID"
)

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


def empty_priority_counts():
    return {
        "URGENT": 0,
        "HIGH": 0,
        "NORMAL": 0,
        "LOW": 0
    }
def parse_task_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime.combine(
            value,
            datetime.min.time()
        )

    text = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
        "%d-%m-%Y %I:%M %p",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                text,
                fmt
            )
        except ValueError:
            continue

    return None



IST = ZoneInfo("Asia/Kolkata")


def normalize_space(value):
    return re.sub(r"\s+", " ", value or "").strip()


def room_sort_key(value):
    text = normalize_space(value)
    match = re.search(r"\d+", text)
    if match:
        return (0, int(match.group()), text.lower())
    return (1, 999, text.lower())


def parse_judge_header(line):
    pattern = re.compile(
        r"^(?P<judge>.+?)\s*"
        r"\((?P<court>[^()]*)\)"
        r"\s*\|\s*Floor:\s*(?P<floor>[^|]*)"
        r"\|\s*Room:\s*(?P<room>.*)$",
        re.IGNORECASE
    )

    match = pattern.match(normalize_space(line))

    if not match:
        return None

    return {
        "judge_name": normalize_space(match.group("judge")),
        "court_name": normalize_space(match.group("court")),
        "floor": normalize_space(match.group("floor")),
        "room": normalize_space(match.group("room")),
    }


def parse_case_line(line):
    text = normalize_space(line)
    match = re.match(r"^(?P<serial>\d+)\.\s+(?P<body>.+)$", text)

    if not match:
        return None

    body = match.group("body")
    previous_date = ""
    stage = ""

    date_match = re.search(
        r"\((\d{1,2}/\d{1,2}/\d{2,4})\)(?:\s+(.*))?$",
        body
    )

    if date_match:
        previous_date = date_match.group(1) or ""
        stage = normalize_space(date_match.group(2) or "")
        body = normalize_space(body[:date_match.start()])

    tokens = body.split()
    case_number = ""

    if tokens and "/" in tokens[0] and any(ch.isdigit() for ch in tokens[0]):
        case_number = tokens[0]
        case_title = normalize_space(body[len(tokens[0]):])
    else:
        case_title = body

    return {
        "case_number": case_number,
        "case_title": case_title,
        "previous_date": previous_date,
        "stage": stage,
    }


def parse_day_cases_pdf_text(text):
    lines = [
        normalize_space(line)
        for line in (text or "").splitlines()
        if normalize_space(line)
    ]

    groups = []
    current_group = None

    ignored_prefixes = (
        "Blackout Dates:",
        "FROM THE OFFICE OF",
        "ADVOCATE LUDHIANA",
        "CAUSE LIST FOR",
    )

    for line in lines:
        if line.startswith(ignored_prefixes):
            continue

        header = parse_judge_header(line)

        if header:
            current_group = {**header, "cases": []}
            groups.append(current_group)
            continue

        case_item = parse_case_line(line)

        if case_item and current_group:
            current_group["cases"].append(case_item)

    return groups


def fetch_advocate_diaries_cause_groups(target_date):
    web = AdvocateWeb()
    pdf_text = web.extract_day_cases_pdf_text(
        target_date.strftime("%Y-%m-%d")
    )

    groups = parse_day_cases_pdf_text(pdf_text)

    if not groups:
        raise Exception(
            "No court groups could be parsed from the Advocate Diaries PDF."
        )

    return groups


def classify_floor(group):
    floor = normalize_space(group.get("floor"))
    court = normalize_space(group.get("court_name")).lower()

    if floor.isdigit():
        number = int(floor)
        if number <= 20:
            return "regular", number
        return "special", number

    if any(
        keyword in court
        for keyword in (
            "msme",
            "juvenile",
            "consumer",
            "tribunal",
            "commission",
        )
    ):
        return "special", 999

    return "special", 999



def normalize_title_key(value):
    text = normalize_space(value).lower()
    text = re.sub(r"\bversus\b|\bvs\.?\b|\bv\/s\b", " vs ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return normalize_space(text)


def special_location_label(group):
    court = normalize_space(group.get("court_name"))
    judge = normalize_space(group.get("judge_name"))
    combined = f"{court} {judge}".lower()

    if "juvenile" in combined or "jublee" in combined:
        return "Juvenile Court"

    if "msme" in combined or "msefc" in combined:
        return "MSME, Ludhiana"

    if "consumer" in combined:
        return "Consumer Commission"

    if "tribunal" in combined:
        return court or "Tribunal"

    return court or judge or "Other Court Location"


def build_title_lookup(case_lookup):
    title_lookup = defaultdict(list)
    seen = set()

    for item in case_lookup.values():
        marker = id(item)

        if marker in seen:
            continue

        seen.add(marker)
        key = normalize_title_key(item.get("case_title"))

        if key:
            title_lookup[key].append(item)

    return title_lookup


def find_local_case(case_item, case_lookup, title_lookup):
    case_key = normalize_space(
        case_item.get("case_number")
    ).lower()

    if case_key:
        local = case_lookup.get(case_key)

        if local:
            return local

    title_key = normalize_title_key(
        case_item.get("case_title")
    )

    if not title_key:
        return None

    exact_matches = title_lookup.get(
        title_key,
        []
    )

    if len(exact_matches) == 1:
        return exact_matches[0]

    return None


def build_court_movement_summary(groups):
    floor_counts = defaultdict(int)
    special_counts = defaultdict(int)

    for group in groups:
        category, floor_number = classify_floor(group)
        count = len(group.get("cases", []))

        if category == "regular":
            floor_counts[floor_number] += count
        else:
            special_counts[
                special_location_label(group)
            ] += count

    lines = [
        "🚶 COURT MOVEMENT PLAN",
        ""
    ]

    for floor_number in sorted(floor_counts):
        label = (
            "Ground Floor"
            if floor_number == 0
            else f"Floor {floor_number}"
        )

        lines.append(
            f"• {label}: "
            f"{floor_counts[floor_number]} matter(s)"
        )

    for label in sorted(special_counts):
        lines.append(
            f"• {label}: "
            f"{special_counts[label]} matter(s)"
        )

    return "\n".join(lines)


def build_staff_deployment_summary(groups, task_lookup):
    deployment = defaultdict(
        lambda: defaultdict(int)
    )

    for group in groups:
        category, floor_number = classify_floor(group)

        if category == "regular":
            location = (
                "Ground Floor"
                if floor_number == 0
                else f"Floor {floor_number}"
            )
        else:
            location = special_location_label(group)

        for case_item in group.get("cases", []):
            key = normalize_space(
                case_item.get("case_number")
            ).lower()

            for task in task_lookup.get(key, []):
                staff = normalize_space(
                    task.get("staff")
                )

                if staff:
                    deployment[staff][location] += 1

    if not deployment:
        return (
            "👥 SUGGESTED STAFF DEPLOYMENT\n\n"
            "No case-linked staff assignments found."
        )

    lines = [
        "👥 SUGGESTED STAFF DEPLOYMENT",
        ""
    ]

    for staff in sorted(deployment):
        ordered = sorted(
            deployment[staff].items(),
            key=lambda item: (
                -item[1],
                item[0].lower()
            )
        )

        location_text = ", ".join(
            f"{location} ({count})"
            for location, count in ordered
        )

        lines.append(
            f"• {staff}: {location_text}"
        )

    return "\n".join(lines)


def build_floor_wise_cause_list(
    groups,
    case_lookup,
    task_lookup
):
    regular = defaultdict(list)
    special = []
    total_cases = 0
    title_lookup = build_title_lookup(
        case_lookup
    )

    for group in groups:
        total_cases += len(
            group["cases"]
        )

        category, floor_number = classify_floor(
            group
        )

        if category == "regular":
            regular[floor_number].append(
                group
            )
        else:
            special.append(
                group
            )

    lines = [
        "⚖️ FLOOR-WISE CAUSE LIST",
        "",
        f"📌 Total Matters: {total_cases}",
        f"👨‍⚖️ Courts/Judges: {len(groups)}",
        "",
    ]

    running_number = 1

    def append_case(case_item):
        nonlocal running_number

        case_no = (
            case_item["case_number"]
            or "Case number not recorded"
        )

        lines.append(
            f"{running_number}. {case_no}"
        )

        lines.append(
            f"   {case_item['case_title'] or '-'}"
        )

        if case_item["stage"]:
            lines.append(
                f"   📝 Stage: "
                f"{case_item['stage']}"
            )

        local = find_local_case(
            case_item,
            case_lookup,
            title_lookup
        )

        if local and local.get("client_name"):
            lines.append(
                f"   👤 Client: "
                f"{local['client_name']}"
            )

        key = normalize_space(
            case_item["case_number"]
        ).lower()

        tasks = (
            task_lookup.get(key, [])
            if key
            else []
        )

        if tasks:
            primary = tasks[0]

            if primary.get("staff"):
                lines.append(
                    f"   👥 Responsible: "
                    f"{primary['staff']}"
                )

            if primary.get("task"):
                lines.append(
                    f"   📋 Pending: "
                    f"{primary['task']}"
                )

        if local:
            lines.append(
                "   📂 Drive: "
                + (
                    "Ready"
                    if local.get("drive_folder_id")
                    else "Not linked"
                )
            )

        lines.append("")
        running_number += 1

    def append_judge_group(group):
        lines.extend([
            f"👨‍⚖️ "
            f"{group.get('judge_name') or 'Judge not recorded'}",
            f"⚖️ "
            f"{group.get('court_name') or 'Court not recorded'}",
            f"📌 Matters: "
            f"{len(group['cases'])}",
            "",
        ])

        for case_item in group["cases"]:
            append_case(case_item)

    for floor_number in sorted(regular):
        floor_label = (
            "GROUND FLOOR"
            if floor_number == 0
            else f"FLOOR {floor_number}"
        )

        floor_groups = sorted(
            regular[floor_number],
            key=lambda item: (
                room_sort_key(
                    item.get("room")
                ),
                normalize_space(
                    item.get("judge_name")
                ).lower(),
            )
        )

        floor_total = sum(
            len(group["cases"])
            for group in floor_groups
        )

        lines.extend([
            "══════════════════════",
            f"🏢 {floor_label}",
            f"📌 Floor Matters: {floor_total}",
            "",
        ])

        room_groups = defaultdict(list)

        for group in floor_groups:
            room = (
                group.get("room")
                or "Not recorded"
            )

            room_groups[room].append(group)

        for room in sorted(
            room_groups,
            key=room_sort_key
        ):
            room_total = sum(
                len(group["cases"])
                for group in room_groups[room]
            )

            lines.extend([
                f"🏛 ROOM {room}",
                f"📌 Room Matters: {room_total}",
                "",
            ])

            for group in room_groups[room]:
                append_judge_group(group)

            lines.extend([
                "──────────────",
                ""
            ])

    if special:
        lines.extend([
            "══════════════════════",
            "🏛 SPECIAL COURTS / OTHER LOCATIONS",
            "",
        ])

        special_by_location = defaultdict(list)

        for group in special:
            special_by_location[
                special_location_label(group)
            ].append(group)

        for location in sorted(special_by_location):
            location_groups = special_by_location[
                location
            ]

            location_total = sum(
                len(group["cases"])
                for group in location_groups
            )

            lines.extend([
                f"📍 {location}",
                f"📌 Location Matters: "
                f"{location_total}",
                "",
            ])

            for group in sorted(
                location_groups,
                key=lambda item: (
                    room_sort_key(
                        item.get("room")
                    ),
                    normalize_space(
                        item.get("judge_name")
                    ).lower(),
                )
            ):
                room = (
                    group.get("room")
                    or "Not recorded"
                )

                if room != "Not recorded":
                    lines.append(
                        f"🏛 Room {room}"
                    )

                append_judge_group(group)

            lines.extend([
                "──────────────",
                ""
            ])

    return "\n".join(lines), total_cases



def _dashboard_table_columns(cur, table_name):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        """,
        (table_name,),
    )
    return {row[0] for row in cur.fetchall()}


def _load_morning_dashboard_database(today):
    """Load Sprint 10 dashboard data without assuming every migration exists."""
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    cur = conn.cursor()

    try:
        task_columns = _dashboard_table_columns(cur, "tasks")
        case_columns = _dashboard_table_columns(cur, "cases")

        task_rows = []
        case_rows = []
        warnings = []

        if task_columns:
            def task_expr(column, fallback="NULL"):
                return column if column in task_columns else fallback

            status_filter = (
                "WHERE UPPER(COALESCE(status, 'PENDING')) = 'PENDING'"
                if "status" in task_columns
                else ""
            )
            priority_expr = (
                "COALESCE(priority, 'NORMAL')"
                if "priority" in task_columns
                else "'NORMAL'"
            )
            order_parts = []
            if "priority" in task_columns:
                order_parts.append(
                    "CASE UPPER(COALESCE(priority, 'NORMAL')) "
                    "WHEN 'URGENT' THEN 1 WHEN 'HIGH' THEN 2 "
                    "WHEN 'NORMAL' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END"
                )
            if "due_at" in task_columns:
                order_parts.append("due_at ASC NULLS LAST")
            if "created_at" in task_columns:
                order_parts.append("created_at ASC NULLS LAST")
            if "id" in task_columns:
                order_parts.append("id ASC")
            order_sql = " ORDER BY " + ", ".join(order_parts) if order_parts else ""

            cur.execute(
                f"""
                SELECT
                    {task_expr('id', '0')},
                    {task_expr('assigned_to', "'Unassigned'")},
                    {task_expr('case_number', "''")},
                    {task_expr('task', "''")},
                    {task_expr('deadline')},
                    {task_expr('due_at')},
                    {task_expr('source_type', "''")},
                    {task_expr('notes', "''")},
                    {task_expr('created_at')},
                    {priority_expr} AS priority
                FROM tasks
                {status_filter}
                {order_sql}
                """
            )
            task_rows = cur.fetchall()
        else:
            warnings.append("Tasks table is unavailable; workload figures were omitted.")

        if case_columns:
            def case_expr(column, fallback="''"):
                return column if column in case_columns else fallback

            cur.execute(
                f"""
                SELECT
                    {case_expr('case_number')},
                    {case_expr('case_id')},
                    {case_expr('client_name')},
                    {case_expr('case_title')},
                    {case_expr('drive_folder_id')}
                FROM cases
                """
            )
            case_rows = cur.fetchall()
        else:
            warnings.append("Cases table is unavailable; case links were omitted.")

        return task_rows, case_rows, warnings
    finally:
        cur.close()
        conn.close()


def build_morning_dashboard():
    """Build the Sprint 10 briefing with independently resilient data sources."""
    now = datetime.now(IST)
    today = now.date()
    logger = logging.getLogger(__name__)

    task_rows = []
    case_rows = []
    source_warnings = []
    database_live = False
    advocate_diaries_live = False

    try:
        task_rows, case_rows, database_warnings = _load_morning_dashboard_database(today)
        source_warnings.extend(database_warnings)
        database_live = True
    except Exception as exc:
        logger.exception("Morning dashboard database source failed")
        source_warnings.append(
            f"Office database unavailable ({type(exc).__name__}); task and case figures were omitted."
        )

    case_lookup = {}
    for (
        case_number,
        case_id,
        client_name,
        case_title,
        drive_folder_id
    ) in case_rows:
        item = {
            "client_name": client_name or "",
            "case_title": case_title or "",
            "drive_folder_id": drive_folder_id or "",
        }
        for key in {
            normalize_space(case_number).lower(),
            normalize_space(case_id).lower(),
        }:
            if key:
                case_lookup[key] = item

    task_lookup = defaultdict(list)
    staff_summary = {}
    office_priority_counts = empty_priority_counts()
    overdue_tasks = []
    due_today_tasks = []
    ad_pending_count = 0

    for (
        task_id,
        assigned_to,
        case_number,
        task_text,
        deadline,
        due_at,
        source_type,
        notes,
        created_at,
        priority
    ) in task_rows:
        staff_name = assigned_to or "Unassigned"
        priority_value = normalize_priority(priority)
        office_priority_counts[priority_value] += 1

        if staff_name not in staff_summary:
            staff_summary[staff_name] = {
                "total": 0,
                "overdue": 0,
                "due_today": 0,
                "ad_tasks": 0,
                "manual_tasks": 0,
                "priority_counts": empty_priority_counts(),
            }

        data = staff_summary[staff_name]
        data["total"] += 1
        data["priority_counts"][priority_value] += 1

        if source_type == "advocate_diaries_work":
            data["ad_tasks"] += 1
            ad_pending_count += 1
        else:
            data["manual_tasks"] += 1

        key = normalize_space(case_number).lower()
        if key:
            task_lookup[key].append({
                "id": task_id,
                "staff": staff_name,
                "task": task_text or "",
                "priority": priority_value,
            })

        parsed_deadline = parse_task_date(due_at or deadline)
        item = {
            "id": task_id,
            "staff": staff_name,
            "case_number": case_number or "",
            "task": task_text or "",
            "case_title": notes or "",
            "source_type": source_type or "",
            "deadline": parsed_deadline,
            "priority": priority_value,
        }

        if not parsed_deadline:
            continue
        if parsed_deadline.date() < today:
            overdue_tasks.append(item)
            data["overdue"] += 1
        elif parsed_deadline.date() == today:
            due_today_tasks.append(item)
            data["due_today"] += 1

    groups = []
    cause_text = (
        "⚖️ FLOOR-WISE CAUSE LIST\n\n"
        "⚠️ Advocate Diaries data is currently unavailable."
    )
    total_hearings = None

    try:
        groups = fetch_advocate_diaries_cause_groups(today)
        cause_text, total_hearings = build_floor_wise_cause_list(
            groups,
            case_lookup,
            task_lookup
        )
        advocate_diaries_live = True
    except Exception as exc:
        logger.exception("Morning dashboard Advocate Diaries source failed")
        source_warnings.append(
            f"Advocate Diaries unavailable ({type(exc).__name__}); cause list and court movement were omitted."
        )

    urgent_staff = []
    for staff_name, data in staff_summary.items():
        urgent_count = data["priority_counts"]["URGENT"]
        if urgent_count:
            urgent_staff.append(f"{staff_name}: {urgent_count}")

    hearing_text = str(total_hearings) if total_hearings is not None else "Unavailable"
    message = (
        "🌅 LAW OFFICE MORNING DASHBOARD\n"
        f"📅 {today.strftime('%d-%m-%Y')}\n"
        f"🕘 Refreshed: {now.strftime('%I:%M %p')} IST\n\n"
        "📡 DATA STATUS\n"
        f"{'✅' if database_live else '⚠️'} Office Database: "
        f"{'Live' if database_live else 'Unavailable'}\n"
        f"{'✅' if advocate_diaries_live else '⚠️'} Advocate Diaries: "
        f"{'Live' if advocate_diaries_live else 'Unavailable'}\n\n"
        f"⚖️ Today's Hearings: {hearing_text}\n"
        f"🔴 Overdue Tasks: {len(overdue_tasks) if database_live else 'Unavailable'}\n"
        f"🟠 Tasks Due Today: {len(due_today_tasks) if database_live else 'Unavailable'}\n"
        f"📘 Pending AD-linked Tasks: {ad_pending_count if database_live else 'Unavailable'}\n"
        f"📋 Total Pending Tasks: {len(task_rows) if database_live else 'Unavailable'}\n\n"
    )

    if database_live:
        message += (
            f"🔴 Urgent: {office_priority_counts['URGENT']}\n"
            f"🟠 High: {office_priority_counts['HIGH']}\n"
            f"🔵 Normal: {office_priority_counts['NORMAL']}\n"
            f"⚪ Low: {office_priority_counts['LOW']}\n\n"
        )

    if source_warnings:
        message += "⚠️ SOURCE NOTICES\n" + "\n".join(
            f"• {warning}" for warning in source_warnings
        ) + "\n\n"

    if database_live:
        if urgent_staff:
            message += "🔥 OFFICE FOCUS\n" + "\n".join(
                f"• {item}" for item in urgent_staff
            ) + "\n\n"
        else:
            message += "🔥 OFFICE FOCUS\nNo urgent tasks pending.\n\n"

        message += "👥 STAFF-WISE WORKLOAD\n\n"
        if staff_summary:
            for staff_name in sorted(staff_summary):
                data = staff_summary[staff_name]
                counts = data["priority_counts"]
                message += (
                    f"👤 {staff_name.upper()}\n"
                    f"📋 Pending: {data['total']}\n"
                    f"🔴 Urgent: {counts['URGENT']}\n"
                    f"🟠 High: {counts['HIGH']}\n"
                    f"🔵 Normal: {counts['NORMAL']}\n"
                    f"⚪ Low: {counts['LOW']}\n"
                    f"🔴 Overdue: {data['overdue']}\n"
                    f"🟠 Due Today: {data['due_today']}\n"
                    f"📘 AD Work: {data['ad_tasks']}\n"
                    f"📝 Manual: {data['manual_tasks']}\n\n"
                )
        else:
            message += "✅ No pending tasks.\n\n"

    if advocate_diaries_live:
        message += build_court_movement_summary(groups) + "\n\n"
        if database_live:
            message += build_staff_deployment_summary(groups, task_lookup) + "\n\n"
        message += cause_text + "\n\n"
    else:
        message += cause_text + "\n\n"

    if database_live:
        message += "🔴 OVERDUE TASKS\n\n"
        if overdue_tasks:
            for item in overdue_tasks:
                icon = priority_icon(item["priority"])
                message += f"{icon} Task #{item['id']}\n👤 {item['staff']}\n"
                if item["case_number"]:
                    message += f"🔢 {item['case_number']}\n"
                message += f"📝 {item['task']}\n"
                if item["deadline"]:
                    message += f"⏰ Due: {item['deadline'].strftime('%d-%m-%Y %I:%M %p')}\n"
                message += "\n"
        else:
            message += "✅ No overdue tasks.\n\n"

        message += "🟠 TASKS DUE TODAY\n\n"
        if due_today_tasks:
            for item in due_today_tasks:
                icon = priority_icon(item["priority"])
                message += f"{icon} Task #{item['id']}\n👤 {item['staff']}\n"
                if item["case_number"]:
                    message += f"🔢 {item['case_number']}\n"
                message += f"📝 {item['task']}\n"
                if item["deadline"]:
                    message += f"⏰ Due: {item['deadline'].strftime('%d-%m-%Y %I:%M %p')}\n"
                message += "\n"
        else:
            message += "No tasks are due today.\n\n"

    message += (
        "Commands:\n"
        "/pendingtasks — all pending tasks\n"
        "/taskdetails TASK_ID — task details\n"
        "/taskhistory STAFF pending — staff workload"
    )
    return message


async def send_dashboard_message(
    context,
    chat_id,
    message
):
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

            message = message[
                split_at:
            ].lstrip()

        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            disable_web_page_preview=True
        )


async def morningdashboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        message = build_morning_dashboard()

        await send_dashboard_message(
            context,
            update.effective_chat.id,
            message
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Morning dashboard failed:\n"
            f"{type(e).__name__}: {e}"
        )


async def morning_dashboard_job(context):
    if not OFFICE_GROUP_CHAT_ID:
        print(
            "MORNING DASHBOARD SKIPPED: "
            "OFFICE_GROUP_CHAT_ID is not set"
        )
        return

    try:
        message = build_morning_dashboard()

        await send_dashboard_message(
            context,
            int(OFFICE_GROUP_CHAT_ID),
            message
        )

        print(
            "MORNING DASHBOARD SENT"
        )

    except Exception as e:
        print(
            "MORNING DASHBOARD FAILED: "
            f"{type(e).__name__}: {e}"
        )

def build_staff_morning_briefs():
    now = datetime.now()
    today = now.date()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                sa.staff_name,
                sa.telegram_user_id,
                t.id,
                t.case_number,
                t.task,
                t.deadline,
                t.due_at,
                t.source_type,
                t.source_work_id,
                t.notes,
                t.created_at,
                COALESCE(
                    t.priority,
                    'NORMAL'
                ) AS priority

            FROM staff_accounts sa

            LEFT JOIN tasks t
                ON LOWER(TRIM(t.assigned_to))
                   =
                   LOWER(TRIM(sa.staff_name))

               AND UPPER(t.status) = 'PENDING'

            WHERE sa.is_active = TRUE

            ORDER BY
                sa.staff_name ASC,

                CASE UPPER(
                    COALESCE(
                        t.priority,
                        'NORMAL'
                    )
                )
                    WHEN 'URGENT' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'NORMAL' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END,

                COALESCE(
                    t.due_at,

                    CASE
                        WHEN TRIM(
                            COALESCE(
                                t.deadline,
                                ''
                            )
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(t.deadline),
                            'YYYY-MM-DD HH24:MI:SS'
                        )

                        WHEN TRIM(
                            COALESCE(
                                t.deadline,
                                ''
                            )
                        ) ~
                        '^\\d{4}-\\d{2}-\\d{2}$'
                        THEN TO_TIMESTAMP(
                            TRIM(t.deadline),
                            'YYYY-MM-DD'
                        )

                        WHEN TRIM(
                            COALESCE(
                                t.deadline,
                                ''
                            )
                        ) ~
                        '^\\d{2}-\\d{2}-\\d{4}$'
                        THEN TO_TIMESTAMP(
                            TRIM(t.deadline),
                            'DD-MM-YYYY'
                        )

                        ELSE NULL
                    END
                ) ASC NULLS LAST,

                t.created_at ASC NULLS LAST,
                t.id ASC
        """)

        rows = cur.fetchall()

    finally:
        cur.close()
        conn.close()

    staff_data = {}

    for (
        staff_name,
        telegram_user_id,
        task_id,
        case_number,
        task_text,
        deadline,
        due_at,
        source_type,
        source_work_id,
        notes,
        created_at,
        priority
    ) in rows:

        if not telegram_user_id:
            continue

        key = str(
            telegram_user_id
        )

        if key not in staff_data:
            staff_data[key] = {
                "staff_name": staff_name,
                "telegram_user_id": (
                    telegram_user_id
                ),
                "tasks": []
            }

        if task_id is None:
            continue

        staff_data[key]["tasks"].append({
            "id": task_id,
            "case_number": (
                case_number
                or ""
            ),
            "task": task_text or "",
            "deadline": deadline,
            "due_at": due_at,
            "source_type": (
                source_type
                or ""
            ),
            "source_work_id": (
                source_work_id
            ),
            "case_title": notes or "",
            "created_at": created_at,
            "priority": (
                normalize_priority(
                    priority
                )
            )
        })

    briefs = []

    for data in staff_data.values():
        staff_name = data["staff_name"]
        tasks = data["tasks"]

        unique_tasks = []
        seen_task_ids = set()
        seen_ad_work_ids = set()

        for item in tasks:
            task_id = item["id"]
            source_work_id = item[
                "source_work_id"
            ]

            if task_id in seen_task_ids:
                continue

            if (
                item["source_type"]
                == "advocate_diaries_work"
                and source_work_id
                and str(source_work_id)
                in seen_ad_work_ids
            ):
                continue

            seen_task_ids.add(
                task_id
            )

            if source_work_id:
                seen_ad_work_ids.add(
                    str(source_work_id)
                )

            unique_tasks.append(
                item
            )

        priority_counts = (
            empty_priority_counts()
        )

        overdue_count = 0
        due_today_count = 0

        for item in unique_tasks:
            priority_counts[
                item["priority"]
            ] += 1

            deadline_value = (
                item["due_at"]
                or item["deadline"]
            )

            parsed_deadline = (
                parse_task_date(
                    deadline_value
                )
            )

            item[
                "parsed_deadline"
            ] = parsed_deadline

            if not parsed_deadline:
                continue

            if (
                parsed_deadline.date()
                < today
            ):
                overdue_count += 1

            elif (
                parsed_deadline.date()
                == today
            ):
                due_today_count += 1

        keyboard_rows = []

        message = (
            "🌅 YOUR MORNING WORK BRIEF\n"
            f"👤 {staff_name.upper()}\n"
            f"📅 {today.strftime('%d-%m-%Y')}\n\n"

            f"📋 Pending Tasks: "
            f"{len(unique_tasks)}\n"

            f"🔴 Urgent: "
            f"{priority_counts['URGENT']}\n"

            f"🟠 High: "
            f"{priority_counts['HIGH']}\n"

            f"🔵 Normal: "
            f"{priority_counts['NORMAL']}\n"

            f"⚪ Low: "
            f"{priority_counts['LOW']}\n\n"

            f"🔴 Overdue: "
            f"{overdue_count}\n"

            f"🟠 Due Today: "
            f"{due_today_count}\n\n"
        )

        if not unique_tasks:
            message += (
                "✅ You have no pending tasks.\n"
            )

        else:
            for item in unique_tasks:
                source_type = item[
                    "source_type"
                ]

                priority_value = item[
                    "priority"
                ]

                icon = priority_icon(
                    priority_value
                )

                if (
                    source_type
                    == "advocate_diaries_work"
                ):
                    source_label = (
                        "Advocate Diaries Work"
                    )

                elif source_type == "manual":
                    source_label = (
                        "Manual Task"
                    )

                else:
                    source_label = (
                        source_type
                        or "Task"
                    )

                message += (
                    f"{icon} Task #{item['id']}\n"
                    f"📌 Source: "
                    f"{source_label}\n"
                )

                if item["case_title"]:
                    message += (
                        f"⚖️ "
                        f"{item['case_title']}\n"
                    )

                if item["case_number"]:
                    message += (
                        f"🔢 "
                        f"{item['case_number']}\n"
                    )

                message += (
                    f"📝 {item['task']}\n"
                )

                parsed_deadline = item[
                    "parsed_deadline"
                ]

                if parsed_deadline:
                    if (
                        source_type
                        == "advocate_diaries_work"
                    ):
                        message += (
                            f"📅 Next Hearing: "
                            f"{parsed_deadline.strftime('%d-%m-%Y')}\n"
                        )

                    else:
                        message += (
                            f"⏰ Deadline: "
                            f"{parsed_deadline.strftime('%d-%m-%Y %I:%M %p')}\n"
                        )

                message += "\n"

                keyboard_rows.append([
                    InlineKeyboardButton(
                        f"📋 Details #{item['id']}",
                        callback_data=(
                            f"taskdetails:"
                            f"{item['id']}"
                        )
                    ),
                    InlineKeyboardButton(
                        f"✅ Complete #{item['id']}",
                        callback_data=(
                            f"completetask:"
                            f"{item['id']}"
                        )
                    )
                ])

        message += (
            "Use /mytasks to view all "
            "pending tasks."
        )

        briefs.append({
            "telegram_user_id": data[
                "telegram_user_id"
            ],
            "staff_name": staff_name,
            "message": message,
            "reply_markup": (
                InlineKeyboardMarkup(
                    keyboard_rows
                )
                if keyboard_rows
                else None
            )
        })

    return briefs
    
async def staff_morning_brief_job(context):
    try:
        briefs = build_staff_morning_briefs()

        sent_count = 0
        failed_count = 0

        for brief in briefs:
            try:
                await send_staff_brief_message(
                    context=context,
                    chat_id=brief["telegram_user_id"],
                    message=brief["message"],
                    reply_markup=brief.get(
                        "reply_markup"
                    )
                )

                sent_count += 1

            except Exception as e:
                failed_count += 1

                print(
                    "STAFF MORNING BRIEF FAILED "
                    f"{brief['staff_name']}: "
                    f"{type(e).__name__}: {e}"
                )

        print(
            "STAFF MORNING BRIEFS COMPLETED: "
            f"sent={sent_count}, "
            f"failed={failed_count}"
        )

    except Exception as e:
        print(
            "STAFF MORNING BRIEF JOB FAILED: "
            f"{type(e).__name__}: {e}"
        )

async def test_staff_morning_briefs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        briefs = build_staff_morning_briefs()

        if not briefs:
            await update.effective_message.reply_text(
                "No active linked staff accounts found."
            )
            return

        sent_count = 0
        failed_count = 0

        for brief in briefs:
            try:
                await send_staff_brief_message(
                    context=context,
                    chat_id=brief["telegram_user_id"],
                    message=brief["message"],
                    reply_markup=brief.get(
                        "reply_markup"
                    )
                )
                sent_count += 1

            except Exception as e:
                failed_count += 1

                await update.effective_message.reply_text(
                    f"⚠️ Brief failed for "
                    f"{brief['staff_name']}:\n"
                    f"{type(e).__name__}: {e}"
                )

        await update.effective_message.reply_text(
            f"✅ Staff morning brief test completed.\n\n"
            f"Sent: {sent_count}\n"
            f"Failed: {failed_count}"
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Staff morning brief test failed:\n"
            f"{type(e).__name__}: {e}"
        )

async def send_staff_brief_message(
    context,
    chat_id,
    message,
    reply_markup=None
):
    max_length = 3800
    chunks = []

    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            remaining = ""

        else:
            split_at = remaining.rfind(
                "\n\n",
                0,
                max_length
            )

            if split_at == -1:
                split_at = max_length

            chunks.append(
                remaining[:split_at]
            )

            remaining = remaining[
                split_at:
            ].lstrip()

    for index, chunk in enumerate(chunks):
        is_last_chunk = (
            index == len(chunks) - 1
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            reply_markup=(
                reply_markup
                if is_last_chunk
                else None
            ),
            disable_web_page_preview=True
        )