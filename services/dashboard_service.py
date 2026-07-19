"""Safe dashboard summary service for Sprint 1A.

This first version intentionally does not query attendance, Advocate Diaries,
Google Drive, work or task tables. It provides a stable presentation contract
without touching modules that are already working in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    hearings_today: int | None = None
    appointments_today: int | None = None
    pending_works: int | None = None
    pending_tasks: int | None = None
    staff_present: int | None = None
    staff_total: int | None = None
    documents_today: int | None = None
    notifications: int | None = None


def get_dashboard_summary(telegram_user_id: int | None = None) -> DashboardSummary:
    """Return the Sprint 1A dashboard contract.

    ``telegram_user_id`` is accepted now so live role-aware summaries can be
    added later without changing the command interface.
    """
    del telegram_user_id
    return DashboardSummary()


def greeting_for_now(now: datetime | None = None) -> str:
    """Return an India-time greeting."""
    current = now.astimezone(IST) if now else datetime.now(IST)
    if current.hour < 12:
        return "Good Morning"
    if current.hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def display_count(value: int | None) -> str:
    return "--" if value is None else str(value)


def display_attendance(present: int | None, total: int | None) -> str:
    if present is None or total is None:
        return "--"
    return f"{present}/{total}"
