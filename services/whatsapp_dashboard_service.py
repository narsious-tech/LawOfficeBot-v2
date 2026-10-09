"""On-demand WhatsApp morning/evening dashboards and owner live controls."""
from __future__ import annotations

import hashlib
from datetime import datetime
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 8
FILE_PAGE_SIZE = 7
FILE_RECIPIENTS = ("Preet", "Priya", "Happy", "Jimmy")
STATUS_LABELS = {
    "LISTED": "⚪ Listed",
    "CALLED": "🟢 Called",
    "PASSED_OVER": "🟡 Passed Over",
    "ADJOURNED": "🔵 Adjourned",
    "ORDER_RESERVED": "🟣 Order Reserved",
    "DISPOSED": "✅ Disposed",
}
OWNER_STATUS_CHOICES = (
    ("CALLED", "Called", "Matter has been called"),
    ("PASSED_OVER", "Passed Over", "Matter passed over"),
    ("ADJOURNED", "Adjourned", "Matter adjourned"),
    ("ORDER_RESERVED", "Order Reserved", "Order has been reserved"),
    ("LISTED", "Reset Listed", "Return to listed status"),
)


def _staff_board(staff: dict[str, Any], evening: bool = False) -> str:
    from commands.dashboard import build_staff_evening_boards, build_staff_morning_briefs

    boards = build_staff_evening_boards() if evening else build_staff_morning_briefs()
    wanted_id = str(staff.get("telegram_user_id") or "")
    wanted_name = str(staff.get("staff_name") or "").strip().casefold()
    for board in boards:
        board_id = str(board.get("telegram_user_id") or "")
        board_name = str(board.get("staff_name") or "").strip().casefold()
        if (wanted_id and board_id == wanted_id) or (wanted_name and board_name == wanted_name):
            message = str(board.get("message") or "")
            return message.replace(
                "Use /mytasks to view all pending tasks.",
                "Send MY WORK to view or complete pending work.",
            ).replace(
                "Use /mytasks to review or complete pending work.",
                "Send MY WORK to review or complete pending work.",
            )[:4000]
    label = "EVENING" if evening else "MORNING"
    return (
        f"{label} DASHBOARD — {staff.get('staff_name') or 'Staff'}\n\n"
        "No personal work data is available. Send MY WORK to refresh your assignments."
    )


def staff_morning_dashboard(staff: dict[str, Any]) -> str:
    return _staff_board(staff, evening=False)


def staff_evening_dashboard(staff: dict[str, Any]) -> str:
    return _staff_board(staff, evening=True)


def owner_morning_dashboard() -> str:
    from commands.dashboard import build_morning_dashboard

    return (build_morning_dashboard() + "\n\nSend LIVE for owner live-hearing control.")[:4000]


def evening_target_plan(day=None):
    """Use the shared office calendar instead of assuming calendar tomorrow."""
    from services.office_calendar_service import manual_evening_plan

    return manual_evening_plan(day or datetime.now(IST).date())


def owner_evening_dashboard() -> str:
    from commands.dashboard import fetch_advocate_diaries_cause_groups
    from commands.evening_dashboard import _flatten_cases

    plan = evening_target_plan()
    target = plan.target_date
    groups, source = fetch_advocate_diaries_cause_groups(target)
    cases = _flatten_cases(groups)
    lines = [
        "🌆 OWNER EVENING DASHBOARD",
        f"📅 Next court day: {target.strftime('%d-%m-%Y')}",
        f"🗓 {plan.heading}",
        f"⚖️ Hearings: {len(cases)}",
        f"🏛 Court groups: {len(groups)}",
        f"🔗 Source: Advocate Diaries {source}",
        "",
    ]
    if not cases:
        lines.append("No hearings are recorded for tomorrow.")
    else:
        for index, case in enumerate(cases[:10], 1):
            lines.extend([
                f"{index}. {case.get('case_number') or '-'}",
                f"   {case.get('case_title') or 'Title not recorded'}",
                f"   {case.get('court') or '-'} · {case.get('purpose') or '-'}",
            ])
        if len(cases) > 10:
            lines.append(f"\n…and {len(cases) - 10} more hearing(s).")
    lines.extend([
        "",
        "Physical-file selection remains in the protected Telegram evening workflow for now.",
        "No paid WhatsApp template was sent.",
    ])
    return "\n".join(lines)[:4000]


def ensure_file_selection_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_file_selection_draft (
                    id BIGSERIAL PRIMARY KEY,
                    owner_phone TEXT NOT NULL,
                    target_date DATE NOT NULL,
                    case_key TEXT NOT NULL,
                    case_number TEXT NOT NULL,
                    case_title TEXT,
                    court TEXT,
                    judge TEXT,
                    floor TEXT,
                    room TEXT,
                    purpose TEXT,
                    selected BOOLEAN NOT NULL DEFAULT FALSE,
                    display_order INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(owner_phone,target_date,case_key)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_file_delivery (
                    id BIGSERIAL PRIMARY KEY,
                    target_date DATE NOT NULL,
                    recipient_telegram_id BIGINT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    recipient_phone TEXT NOT NULL,
                    selection_hash TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    provider_message_ids TEXT,
                    provider_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(target_date,recipient_telegram_id,selection_hash)
                )
            """)
        conn.commit()
    finally:
        conn.close()


def _case_key(target, case: dict[str, Any]) -> str:
    raw = "|".join(str(value or "").strip().casefold() for value in (
        target.isoformat(), case.get("case_number"), case.get("case_title"),
        case.get("court"), case.get("judge"),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def initialize_file_selection(owner_phone: str) -> tuple[Any, int, str]:
    from commands.dashboard import fetch_advocate_diaries_cause_groups
    from commands.evening_dashboard import _flatten_cases

    ensure_file_selection_schema()
    plan = evening_target_plan()
    groups, source = fetch_advocate_diaries_cause_groups(plan.target_date)
    cases = _flatten_cases(groups)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            active_keys = []
            for index, case in enumerate(cases):
                key = _case_key(plan.target_date, case)
                active_keys.append(key)
                cur.execute("""
                    INSERT INTO whatsapp_file_selection_draft(
                        owner_phone,target_date,case_key,case_number,case_title,
                        court,judge,floor,room,purpose,display_order
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(owner_phone,target_date,case_key) DO UPDATE SET
                        case_number=EXCLUDED.case_number,
                        case_title=EXCLUDED.case_title,court=EXCLUDED.court,
                        judge=EXCLUDED.judge,floor=EXCLUDED.floor,
                        room=EXCLUDED.room,purpose=EXCLUDED.purpose,
                        display_order=EXCLUDED.display_order,updated_at=NOW()
                """, (
                    owner_phone, plan.target_date, key, case["case_number"],
                    case["case_title"], case["court"], case["judge"],
                    case["floor"], case["room"], case["purpose"], index,
                ))
            if active_keys:
                cur.execute("""
                    DELETE FROM whatsapp_file_selection_draft
                    WHERE owner_phone=%s AND target_date=%s
                      AND NOT (case_key=ANY(%s))
                """, (owner_phone, plan.target_date, active_keys))
            else:
                cur.execute("""
                    DELETE FROM whatsapp_file_selection_draft
                    WHERE owner_phone=%s AND target_date=%s
                """, (owner_phone, plan.target_date))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return plan, len(cases), source


def _draft_rows(owner_phone: str, target=None) -> tuple[Any, list[dict[str, Any]]]:
    ensure_file_selection_schema()
    target = target or evening_target_plan().target_date
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM whatsapp_file_selection_draft
                WHERE owner_phone=%s AND target_date=%s
                ORDER BY display_order,id
            """, (owner_phone, target))
            return target, [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def build_file_selection_picker(rows: list[dict[str, Any]], page: int = 0):
    pages = max(1, ceil(len(rows) / FILE_PAGE_SIZE))
    page = max(0, min(int(page), pages - 1))
    start = page * FILE_PAGE_SIZE
    visible = rows[start:start + FILE_PAGE_SIZE]
    picker = []
    for row in visible:
        mark = "✅" if row.get("selected") else "⬜"
        picker.append({
            "id": f"owner_files_toggle:{int(row['id'])}:{page}",
            "title": f"{mark} {row.get('case_number') or 'Open case'}",
            "description": (
                f"{row.get('case_title') or 'Title not recorded'} · "
                f"{row.get('purpose') or 'Purpose not recorded'}"
            ),
        })
    if page > 0:
        picker.append({"id": f"owner_files_page:{page - 1}", "title": "Previous Page", "description": "Earlier cases"})
    if page + 1 < pages:
        picker.append({"id": f"owner_files_page:{page + 1}", "title": "Next Page", "description": "More cases"})
    picker.append({"id": "owner_files_review", "title": "Review Selected", "description": "Confirm only the checked files"})
    return page, pages, visible, picker[:10]


def file_selection_board(owner_phone: str, page: int = 0, initialize: bool = False) -> dict[str, Any]:
    plan = evening_target_plan()
    source = None
    if initialize:
        plan, _, source = initialize_file_selection(owner_phone)
    target, rows = _draft_rows(owner_phone, plan.target_date)
    page, pages, visible, picker = build_file_selection_picker(rows, page)
    selected = sum(1 for row in rows if row.get("selected"))
    lines = [
        "📁 SELECT PHYSICAL FILES",
        f"📅 Court date: {target.strftime('%d-%m-%Y')}",
        f"Selected: {selected} of {len(rows)}",
        f"Page {page + 1} of {pages}",
    ]
    if source:
        lines.append(f"Source: Advocate Diaries {source}")
    lines.extend(["", "Tap a case to select or unselect it. Only checked files will be sent."])
    if not rows:
        lines.extend(["", "No hearings are available for the next court day."])
    return {"reply": "\n".join(lines), "rows": picker if rows else []}


def toggle_file_selection(owner_phone: str, row_id: int, page: int = 0) -> dict[str, Any]:
    ensure_file_selection_schema()
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE whatsapp_file_selection_draft
                SET selected=NOT selected,updated_at=NOW()
                WHERE id=%s AND owner_phone=%s
            """, (int(row_id), owner_phone))
        conn.commit()
    finally:
        conn.close()
    return file_selection_board(owner_phone, page)


def clear_file_selection(owner_phone: str) -> dict[str, Any]:
    ensure_file_selection_schema()
    target = evening_target_plan().target_date
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE whatsapp_file_selection_draft
                SET selected=FALSE,updated_at=NOW()
                WHERE owner_phone=%s AND target_date=%s
            """, (owner_phone, target))
        conn.commit()
    finally:
        conn.close()
    return file_selection_board(owner_phone, 0)


def auto_select_files(owner_phone: str) -> dict[str, Any]:
    from commands.evening_dashboard import _should_auto_select

    target, rows = _draft_rows(owner_phone)
    chosen = [int(row["id"]) for row in rows if _should_auto_select(row)]
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE whatsapp_file_selection_draft
                SET selected=(id=ANY(%s)),updated_at=NOW()
                WHERE owner_phone=%s AND target_date=%s
            """, (chosen or [0], owner_phone, target))
        conn.commit()
    finally:
        conn.close()
    return file_selection_board(owner_phone, 0)


def review_file_selection(owner_phone: str) -> dict[str, Any]:
    target, rows = _draft_rows(owner_phone)
    selected = [row for row in rows if row.get("selected")]
    if not selected:
        return {
            "reply": "⚠️ No physical files are selected. Continue selecting cases first.",
            "buttons": [("owner_files_start", "Continue Selecting")],
        }
    lines = [
        "⚠️ CONFIRM PHYSICAL FILE LIST",
        f"📅 {target.strftime('%d-%m-%Y')}",
        f"Selected files: {len(selected)}",
        "",
    ]
    for index, row in enumerate(selected[:12], 1):
        lines.append(f"{index}. {row.get('case_number') or '-'} · {row.get('case_title') or 'Title not recorded'}")
    if len(selected) > 12:
        lines.append(f"…and {len(selected) - 12} more.")
    lines.extend([
        "",
        "Send only these files to all active linked staff on WhatsApp?",
        "No paid template will be used.",
    ])
    return {
        "reply": "\n".join(lines)[:1024],
        "buttons": [
            ("owner_files_confirm", "Confirm Send"),
            ("owner_files_start", "Continue Selecting"),
            ("owner_files_clear", "Clear All"),
        ],
    }


def _selected_file_message(target, rows: list[dict[str, Any]]) -> str:
    lines = [
        "📁 FILES TO BRING TO EVENING OFFICE",
        f"📅 Court date: {target.strftime('%d-%m-%Y')}",
        f"Selected by: Ajay Chawla",
        f"Total selected files: {len(rows)}",
        "",
    ]
    for index, row in enumerate(rows, 1):
        lines.extend([
            f"{index}. {row.get('case_number') or '-'}",
            f"   {row.get('case_title') or 'Title not recorded'}",
            f"   {row.get('court') or '-'} · Floor {row.get('floor') or '-'} · Room {row.get('room') or '-'}",
            f"   Purpose: {row.get('purpose') or '-'}",
        ])
    lines.append("\nPlease arrange and bring only the above-selected physical files.")
    return "\n".join(lines)


def deliver_selected_files(owner_phone: str, owner_id: int | None) -> str:
    from services.role_intelligence_service import save_file_assignments
    from services.whatsapp_cloud import normalize_phone, send_text_message

    target, rows = _draft_rows(owner_phone)
    selected = [row for row in rows if row.get("selected")]
    if not selected:
        return "⚠️ No physical files are selected. Nothing was sent."
    cases = [{key: row.get(key) for key in (
        "case_number", "case_title", "court", "judge", "floor", "room", "purpose"
    )} for row in selected]
    save_file_assignments(target, cases, set(range(len(cases))), owner_id or 0, "Ajay Chawla")
    digest = hashlib.sha256("|".join(str(row["id"]) for row in selected).encode()).hexdigest()
    message = _selected_file_message(target, selected)
    from services.staff_notification_delivery import deliver_staff_whatsapp, file_event_key
    from datetime import timedelta
    expires = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=IST)
    result = deliver_staff_whatsapp(message, file_event_key(target, message), expires_at=expires)
    lines = ["📁 PHYSICAL FILE DELIVERY RESULT", f"Selected files: {len(selected)}"]
    for key, label in (("sent", "Submitted"), ("queued", "Queued until next WhatsApp message"), ("missing", "WhatsApp not linked"), ("failed", "Failed")):
        lines.append(f"{label}: {', '.join(result[key]) or 'None'}")
    lines.append("No paid template was used.")
    return "\n".join(lines)[:4000]


def _page(rows: list[dict[str, Any]], page: int):
    pages = max(1, ceil(len(rows) / PAGE_SIZE))
    page = max(0, min(int(page), pages - 1))
    start = page * PAGE_SIZE
    return page, pages, rows[start:start + PAGE_SIZE]


def live_board(page: int = 0, refresh: bool = False) -> dict[str, Any]:
    from services.live_hearing_service import list_live_hearings, sync_live_hearings

    source = None
    if refresh:
        _, source = sync_live_hearings()
    rows = list_live_hearings()
    page, pages, visible = _page(rows, page)
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "LISTED")
        counts[status] = counts.get(status, 0) + 1
    now = datetime.now(IST)
    lines = [
        "⚖️ OWNER LIVE HEARING CONTROL",
        f"📅 {now:%d-%m-%Y} · {now:%I:%M %p} IST",
        f"Total {len(rows)} · Called {counts.get('CALLED', 0)} · Passed {counts.get('PASSED_OVER', 0)}",
        f"Closed {sum(counts.get(x, 0) for x in ('ADJOURNED', 'ORDER_RESERVED', 'DISPOSED'))}",
        f"Page {page + 1} of {pages}",
    ]
    if source:
        lines.append(f"Synced via Advocate Diaries {source}")
    if not rows:
        lines.extend(["", "No hearings are listed for today."])
        return {"reply": "\n".join(lines), "rows": [], "page": page, "pages": pages}
    lines.append("\nChoose a hearing. Every status change requires confirmation.")
    picker = []
    for row in visible:
        hearing_id = int(row["id"])
        picker.append({
            "id": f"owner_live_open:{hearing_id}:{page}",
            "title": f"#{hearing_id} {row.get('case_number') or 'Open case'}",
            "description": (
                f"{STATUS_LABELS.get(str(row.get('status')), str(row.get('status') or 'Listed'))} · "
                f"{row.get('case_title') or 'Title not recorded'}"
            ),
        })
    if page > 0:
        picker.append({"id": f"owner_live_page:{page - 1}", "title": "Previous Page", "description": "Earlier hearings"})
    if page + 1 < pages:
        picker.append({"id": f"owner_live_page:{page + 1}", "title": "Next Page", "description": "More hearings"})
    return {"reply": "\n".join(lines)[:1024], "rows": picker, "page": page, "pages": pages}


def live_detail(hearing_id: int, page: int = 0) -> dict[str, Any]:
    from services.live_hearing_service import get_live_hearing

    row = get_live_hearing(int(hearing_id))
    if not row:
        return {"reply": "❌ Hearing not found. Send LIVE to refresh.", "rows": []}
    reply = "\n".join([
        "⚖️ LIVE HEARING",
        "",
        f"🔢 {row.get('case_number') or '-'}",
        f"📝 {row.get('case_title') or '-'}",
        f"📍 {row.get('stage') or 'Stage not recorded'}",
        f"👨‍⚖️ {row.get('judge_name') or row.get('court_name') or '-'}",
        f"📌 Floor {row.get('floor') or '-'} · Room {row.get('room') or '-'}",
        f"📊 {STATUS_LABELS.get(str(row.get('status')), str(row.get('status') or '-'))}",
        "",
        "Choose the new status. Completion/outcome entry remains in Telegram.",
    ])
    picker = [
        {
            "id": f"owner_live_choose:{int(hearing_id)}:{status}:{int(page)}",
            "title": title,
            "description": description,
        }
        for status, title, description in OWNER_STATUS_CHOICES
    ]
    picker.append({
        "id": f"owner_live_page:{int(page)}",
        "title": "Back to Live Board",
        "description": "Do not change this hearing",
    })
    return {"reply": reply[:1024], "rows": picker, "hearing": row}


def live_confirmation(hearing_id: int, status: str, page: int = 0) -> dict[str, Any]:
    from services.live_hearing_service import get_live_hearing

    status = str(status or "").upper()
    if status not in {choice[0] for choice in OWNER_STATUS_CHOICES}:
        return {"reply": "❌ Unsupported live-hearing status.", "buttons": []}
    row = get_live_hearing(int(hearing_id))
    if not row:
        return {"reply": "❌ Hearing not found. Send LIVE to refresh.", "buttons": []}
    reply = "\n".join([
        "⚠️ CONFIRM LIVE STATUS",
        "",
        f"🔢 {row.get('case_number') or '-'}",
        f"📝 {row.get('case_title') or '-'}",
        f"Current: {STATUS_LABELS.get(str(row.get('status')), str(row.get('status') or '-'))}",
        f"New: {STATUS_LABELS.get(status, status)}",
        "",
        "Confirm only if this is the present court status.",
    ])
    return {
        "reply": reply,
        "buttons": [
            (f"owner_live_confirm:{int(hearing_id)}:{status}:{int(page)}", "Confirm"),
            (f"owner_live_open:{int(hearing_id)}:{int(page)}", "Cancel"),
        ],
    }


def apply_live_status(hearing_id: int, status: str, changed_by: int | None, page: int = 0) -> dict[str, Any]:
    from services.live_hearing_service import set_live_hearing_status

    status = str(status or "").upper()
    if status not in {choice[0] for choice in OWNER_STATUS_CHOICES}:
        return {"reply": "❌ Unsupported live-hearing status.", "buttons": []}
    row = set_live_hearing_status(int(hearing_id), status, changed_by)
    if not row:
        return {"reply": "❌ Hearing not found. Send LIVE to refresh.", "buttons": []}
    return {
        "reply": (
            "✅ LIVE STATUS UPDATED\n\n"
            f"🔢 {row.get('case_number') or '-'}\n"
            f"📝 {row.get('case_title') or '-'}\n"
            f"📊 {STATUS_LABELS.get(status, status)}"
        ),
        "buttons": [
            (f"owner_live_open:{int(hearing_id)}:{int(page)}", "Open Hearing"),
            (f"owner_live_page:{int(page)}", "Live Board"),
        ],
    }
