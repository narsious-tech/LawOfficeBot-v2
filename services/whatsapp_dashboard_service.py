"""On-demand WhatsApp morning/evening dashboards and owner live controls."""
from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 8
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
