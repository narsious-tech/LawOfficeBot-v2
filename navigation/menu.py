"""Central menu definitions used by the Telegram navigation layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class MenuItem:
    """A visible menu button and its internal route name."""

    label: str
    route: str


CASES: Final = MenuItem("📁 Cases", "cases")
HEARINGS: Final = MenuItem("📅 Hearings", "hearings")
WORKS: Final = MenuItem("📋 Works", "works")
TASKS: Final = MenuItem("✅ Tasks", "tasks")
APPOINTMENTS: Final = MenuItem("📆 Appointments", "appointments")
DOCUMENTS: Final = MenuItem("📂 Documents", "documents")
ATTENDANCE: Final = MenuItem("🕒 Attendance", "attendance")
STAFF: Final = MenuItem("👥 Staff", "staff")
REPORTS: Final = MenuItem("📊 Reports", "reports")
AI_ASSISTANT: Final = MenuItem("🤖 AI Assistant", "ai")
SETTINGS: Final = MenuItem("⚙️ Settings", "settings")
DASHBOARD: Final = MenuItem("🏠 Dashboard", "dashboard")


MAIN_MENU_ROWS: Final[tuple[tuple[MenuItem, ...], ...]] = (
    (CASES, HEARINGS),
    (WORKS, TASKS),
    (APPOINTMENTS, DOCUMENTS),
    (ATTENDANCE, STAFF),
    (REPORTS, AI_ASSISTANT),
    (SETTINGS, DASHBOARD),
)


def get_main_menu_items() -> tuple[MenuItem, ...]:
    """Return all main-menu items in display order."""
    return tuple(item for row in MAIN_MENU_ROWS for item in row)


def route_for_label(label: str) -> str | None:
    """Resolve an exact Telegram button label to its route."""
    normalized = (label or "").strip()
    for item in get_main_menu_items():
        if normalized == item.label:
            return item.route
    return None
