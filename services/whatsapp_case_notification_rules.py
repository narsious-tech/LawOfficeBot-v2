"""Pure classification rules for WhatsApp case notifications."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


CLOSED_WORDS = {
    "closed", "disposed", "dismissed", "withdrawn", "settled", "decided",
    "cancelled", "canceled",
}
ACTION_WORDS = {
    "personal appearance", "personal presence", "appear in person", "presence required",
    "bring document", "bring original", "documents required", "original document",
    "file reply", "file affidavit", "file evidence",
}


def parse_case_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    for candidate in (text, text[:10]):
        for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    match = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", text)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    return None


def is_closed_status(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return any(word in text for word in CLOSED_WORDS)


def is_material_purpose(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return any(word in text for word in ACTION_WORDS)
