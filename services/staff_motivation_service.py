from __future__ import annotations

from datetime import date, datetime


MORNING_QUOTES = (
    "A clear priority completed today is worth more than ten intentions carried forward.",
    "Preparation makes a busy court day feel shorter.",
    "Small tasks closed on time protect the whole office from larger problems.",
    "Begin with the work that will matter most by evening.",
    "A reliable office is built one completed commitment at a time.",
    "Good preparation is quiet; its results are visible in court.",
    "Today’s discipline becomes tomorrow’s confidence.",
    "Finish the important work before the urgent work chooses you.",
)

EVENING_QUOTES = (
    "A proper closing note gives tomorrow a calmer beginning.",
    "The best end to a workday is a truthful record of what remains.",
    "Close what can be closed; clearly hand over what cannot.",
    "Tomorrow improves when today’s loose ends are named.",
    "A completed update is part of completing the work.",
    "An orderly evening saves an anxious morning.",
    "Progress deserves credit; pending work deserves a plan.",
    "Leave the desk with clarity, not assumptions.",
)

PROTECTED_TERMS = (
    "approved leave", "on leave", "blocked", "awaiting client",
    "awaiting instructions", "awaiting order", "awaiting document",
    "external dependency", "portal unavailable", "website unavailable",
    "server unavailable", "system issue", "system down",
)


def _stable_index(day: date, phase: str, staff_name: str, size: int) -> int:
    seed = day.toordinal() + sum(ord(char) for char in f"{phase}:{staff_name.lower()}")
    return seed % size


def daily_quote(day: date, phase: str = "morning", staff_name: str = "") -> str:
    quotes = EVENING_QUOTES if phase.lower() == "evening" else MORNING_QUOTES
    return quotes[_stable_index(day, phase, staff_name, len(quotes))]


def _deadline_date(task: dict):
    value = task.get("parsed_deadline") or task.get("due_at") or task.get("deadline")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _has_protected_reason(tasks: list[dict]) -> bool:
    combined = " ".join(
        f"{task.get('task', '')} {task.get('case_title', '')} {task.get('notes', '')}"
        for task in tasks
    ).lower()
    return any(term in combined for term in PROTECTED_TERMS)


def accountability_snapshot(tasks: list[dict], today: date) -> dict:
    overdue = []
    due_today = []
    for task in tasks:
        deadline = _deadline_date(task)
        if not deadline:
            continue
        if deadline < today:
            overdue.append((task, (today - deadline).days))
        elif deadline == today:
            due_today.append(task)
    return {
        "pending": len(tasks),
        "overdue": overdue,
        "due_today": due_today,
        "protected": _has_protected_reason(tasks),
        "max_overdue_days": max((days for _, days in overdue), default=0),
    }


def _task_ids(tasks) -> str:
    ids = [f"#{task.get('id')}" for task in tasks if task.get("id") is not None]
    return ", ".join(ids[:6]) or "not recorded"


def build_staff_motivation(
    staff_name: str,
    tasks: list[dict],
    today: date,
    phase: str = "morning",
) -> str:
    snapshot = accountability_snapshot(tasks, today)
    quote = daily_quote(today, phase, staff_name)
    heading = "🌟 MORNING FOCUS" if phase.lower() == "morning" else "🌙 EVENING CHECK-OUT"
    lines = [heading, quote, ""]

    if snapshot["protected"]:
        lines.extend([
            "🛡 A recorded blocker or external dependency is present.",
            "Please update the blocker and the next follow-up step; no adverse reminder is applied.",
        ])
    elif snapshot["overdue"]:
        overdue_tasks = [task for task, _ in snapshot["overdue"]]
        count = len(overdue_tasks)
        if snapshot["max_overdue_days"] >= 3 or count >= 2:
            lines.append(
                f"🔴 Firm reminder: {count} task(s) are overdue "
                f"(Task {_task_ids(overdue_tasks)})."
            )
            lines.append(
                "Optimism is useful, but it is not a completion status. "
                "Please complete the work or record a factual update today."
            )
        else:
            lines.append(
                f"🟠 {count} task(s) crossed the deadline "
                f"(Task {_task_ids(overdue_tasks)})."
            )
            lines.append(
                "The deadline has already attended court; the task is still looking for parking."
            )
    elif snapshot["due_today"]:
        lines.append(
            f"🟠 Due today: {len(snapshot['due_today'])} task(s) "
            f"(Task {_task_ids(snapshot['due_today'])})."
        )
        lines.append("Please close them or add an honest progress update before leaving.")
    elif not tasks:
        lines.append("✅ No pending work is recorded. Keep the board current as new work arrives.")
    elif phase.lower() == "evening":
        lines.append(
            f"📋 {len(tasks)} pending task(s) remain. Confirm priorities before closing the day."
        )
    else:
        lines.append(f"📋 {len(tasks)} pending task(s). Begin with the highest-impact item.")

    return "\n".join(lines)
