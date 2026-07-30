"""Pure date-comparison rules for eCourts verification."""
from datetime import date, datetime
from typing import Any


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def classify_dates(
    staff_next: Any,
    ecourts_next: Any,
    staff_last: Any = None,
    ecourts_last: Any = None,
    today: Any = None,
) -> tuple[str, str]:
    local_next = as_date(staff_next)
    remote_next = as_date(ecourts_next)
    local_last = as_date(staff_last)
    remote_last = as_date(ecourts_last)
    reference_date = as_date(today) or date.today()
    if not local_next:
        return "NO_STAFF_DATE", "No operational staff date is recorded."
    if not remote_next:
        return "AWAITING_ECOURTS", "eCourts has not published a next date."
    if local_last and remote_last and remote_last < local_last:
        return "AWAITING_ECOURTS", "The eCourts record is older than the staff update."
    if remote_next < reference_date <= local_next:
        return "AWAITING_ECOURTS", "The published eCourts next date is historical."
    if local_next < reference_date and remote_next < reference_date:
        return (
            "HISTORICAL_STALE",
            "Both next dates are historical; no operational decision is required.",
        )
    if local_next == remote_next:
        return "VERIFIED", "Staff and eCourts dates agree."
    return "DATE_CONFLICT", "The current eCourts date differs from the staff date."
