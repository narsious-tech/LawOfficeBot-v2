"""Shared staff hearing view for Telegram and WhatsApp."""
from __future__ import annotations

from datetime import date
from typing import Any


def fetch_staff_hearings(target_date: date) -> dict[str, Any]:
    from advocate_diaries import AdvocateDiaries
    from commands.dashboard import (
        _normalize_api_groups,
        fetch_advocate_diaries_cause_groups,
    )

    try:
        payload = AdvocateDiaries().daily_cause_list(target_date.isoformat())
        groups = _normalize_api_groups(payload)
        source = "API"
    except Exception:
        groups, source = fetch_advocate_diaries_cause_groups(target_date)
    return {
        "date": target_date,
        "source": str(source),
        "groups": groups or [],
        "total": sum(len(group.get("cases") or []) for group in groups or []),
    }


def hearing_message_chunks(result: dict[str, Any], limit: int = 3900) -> list[str]:
    """Render court-wise messages without splitting an individual matter."""
    target = result["date"]
    total = int(result.get("total") or 0)
    header = (
        "⚖️ LAW OFFICE HEARINGS\n"
        f"📅 {target.strftime('%d-%m-%Y')}\n"
        f"📌 Total matters: {total}\n"
        f"🔗 Source: Advocate Diaries {result.get('source') or '-'}"
    )
    if total == 0:
        return [header + "\n\nNo hearings are listed for this date."]

    blocks: list[str] = []
    serial = 0
    for group in result.get("groups") or []:
        location = " · ".join(
            value for value in (
                str(group.get("court_name") or "Court"),
                str(group.get("judge_name") or ""),
                f"Floor {group.get('floor')}" if group.get("floor") else "",
                f"Room {group.get('room')}" if group.get("room") else "",
            ) if value
        )
        matters = [f"🏛 {location}"]
        for case in group.get("cases") or []:
            serial += 1
            matters.extend([
                f"{serial}. {case.get('case_title') or 'Title not recorded'}",
                f"   🔢 {case.get('case_number') or '-'}",
                f"   📝 {case.get('stage') or 'Purpose not recorded'}",
            ])
        blocks.append("\n".join(matters))

    chunks: list[str] = []
    current = header
    for block in blocks:
        candidate = current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        current = header + "\n\n" + block
        if len(current) > limit:
            current = current[: limit - 40] + "\n\n…additional matters omitted"
    if current:
        chunks.append(current)
    if len(chunks) > 1:
        chunks = [
            f"{chunk}\n\n📄 Part {index}/{len(chunks)}"
            for index, chunk in enumerate(chunks, start=1)
        ]
    return chunks
