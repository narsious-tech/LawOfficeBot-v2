from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class EveningPlan:
    target_date: date
    mode: str
    heading: str


def saturday_number(day: date) -> int:
    return ((day.day - 1) // 7) + 1


def is_full_day_saturday_holiday(day: date) -> bool:
    return day.weekday() == 5 and saturday_number(day) in (2, 4)


def is_working_saturday(day: date) -> bool:
    return day.weekday() == 5 and not is_full_day_saturday_holiday(day)


def is_morning_office_open(day: date) -> bool:
    if day.weekday() == 6:
        return False
    if day.weekday() == 5:
        return is_working_saturday(day)
    return True


def is_evening_office_open(day: date) -> bool:
    # Evening office is closed every Saturday and works Sunday 2:00–6:00 PM.
    return day.weekday() != 5


def next_monday(day: date) -> date:
    days = (7 - day.weekday()) % 7
    if days == 0:
        days = 7
    return day + timedelta(days=days)


def scheduled_evening_plan(day: date) -> EveningPlan | None:
    if day.weekday() in (0, 1, 2, 3):
        target = day + timedelta(days=1)
        return EveningPlan(target, "NEXT_DAY", "Tomorrow's court preparation")

    if day.weekday() == 4:
        saturday = day + timedelta(days=1)
        if is_full_day_saturday_holiday(saturday):
            target = next_monday(day)
            return EveningPlan(
                target,
                "MONDAY_FINALIZATION_FRIDAY",
                "Monday physical-file finalization (Saturday holiday)",
            )
        return EveningPlan(saturday, "NEXT_DAY", "Saturday court preparation")

    # Saturday has no evening office. Sunday uses the arrival workflow.
    return None


def working_saturday_monday_plan(day: date) -> EveningPlan | None:
    if not is_working_saturday(day):
        return None
    return EveningPlan(
        next_monday(day),
        "MONDAY_FINALIZATION_SATURDAY",
        "Monday physical-file finalization",
    )


def manual_evening_plan(day: date) -> EveningPlan:
    return (
        scheduled_evening_plan(day)
        or working_saturday_monday_plan(day)
        or EveningPlan(next_monday(day), "MONDAY_REVIEW", "Monday physical-file review")
    )


def morning_closure_message(day: date) -> str:
    if day.weekday() == 6:
        monday = day + timedelta(days=1)
        return (
            "🏛 OFFICE CALENDAR\n\n"
            "Sunday morning office is closed.\n"
            "🌆 Evening office: 2:00 PM–6:00 PM\n"
            f"📁 Monday files ({monday:%d %b %Y}) must reach the evening office by 2:00 PM."
        )
    return (
        "🏛 OFFICE CALENDAR\n\n"
        f"{saturday_number(day)} Saturday is a full-day office holiday.\n"
        "🌆 Evening office is also closed.\n"
        "📁 Monday physical files were finalized on Friday and must reach "
        "the Sunday evening office by 2:00 PM."
    )
