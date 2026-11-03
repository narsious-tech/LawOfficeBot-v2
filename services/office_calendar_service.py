from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class EveningPlan:
    target_date: date
    mode: str
    heading: str


# Official 2026 Subordinate Courts calendar plus the two local holidays
# declared for the Ludhiana Sessions Division. General holiday libraries do
# not contain Sessions Division local holidays and differ on some court dates.
_DISTRICT_COURT_HOLIDAYS_2026 = {
    date(2026, 1, 1): "New Year Day",
    date(2026, 1, 26): "Republic Day",
    date(2026, 2, 1): "Guru Ravi Dass Jayanti",
    date(2026, 2, 15): "Maha Shivratri",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 4): "Hola",
    date(2026, 3, 21): "Id-ul-Fitr",
    date(2026, 3, 26): "Ram Navami",
    date(2026, 3, 31): "Mahavir Jayanti",
    date(2026, 4, 14): "Baisakhi / Dr. Ambedkar's Jayanti",
    date(2026, 5, 28): "Id-ul-Zuha (Bakrid)",
    date(2026, 6, 18): "Martyrdom Day of Sri Guru Arjan Dev Ji",
    date(2026, 8, 15): "Independence Day",
    date(2026, 9, 4): "Sri Krishan Janmashtami",
    date(2026, 10, 2): "Mahatma Gandhi's Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 10, 26): "Maharishi Valmiki Jayanti",
    date(2026, 10, 29): "Karwa Chauth (Ludhiana local court holiday)",
    date(2026, 11, 7): "Chhoti Diwali (Ludhiana local court holiday)",
    date(2026, 11, 8): "Diwali",
    date(2026, 11, 9): "Vishwakarma Day",
    date(2026, 11, 24): "Birthday of Sri Guru Nanak Dev Ji",
    date(2026, 12, 14): "Martyrdom Day of Sri Guru Teg Bahadur Ji",
    date(2026, 12, 25): "Christmas",
    **{date(2026, 12, day): "Winter Spell" for day in range(26, 32)},
}


def court_holiday_name(day: date) -> str | None:
    """Return the court holiday, including Ludhiana local declarations."""
    overrides = os.getenv("COURT_HOLIDAY_OVERRIDES", "").strip()
    if overrides:
        try:
            value = json.loads(overrides).get(day.isoformat())
            if value:
                return str(value).strip()
        except (ValueError, TypeError, AttributeError):
            pass

    official = _DISTRICT_COURT_HOLIDAYS_2026.get(day)
    if official:
        return official

    try:
        import holidays

        calendar = holidays.country_holidays(
            "IN",
            subdiv=os.getenv("COURT_HOLIDAY_SUBDIV", "PB").strip() or "PB",
            years=[day.year],
            language="en_IN",
        )
        return str(calendar.get(day) or "").strip() or None
    except Exception:
        return None


def saturday_number(day: date) -> int:
    return ((day.day - 1) // 7) + 1


def is_full_day_saturday_holiday(day: date) -> bool:
    return day.weekday() == 5 and saturday_number(day) in (2, 4)


def is_working_saturday(day: date) -> bool:
    return day.weekday() == 5 and not is_full_day_saturday_holiday(day)


def is_morning_office_open(day: date) -> bool:
    if court_holiday_name(day):
        return False
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
    holiday = court_holiday_name(day)
    if holiday:
        return (
            "🎉 COURT HOLIDAY\n\n"
            f"{holiday}\n"
            f"📅 {day:%d-%m-%Y}\n\n"
            "District Court morning operations and the routine cause-list board are closed.\n"
            "Any specially assigned or urgent matter must still be checked separately."
        )
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
