from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from commands.dashboard import fetch_advocate_diaries_cause_groups, normalize_space

IST = ZoneInfo("Asia/Kolkata")
MESSAGE_LIMIT = 3800


def _number(value, fallback=999):
    text = normalize_space(value)
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else fallback


def _usable_groups(groups):
    cleaned = []
    for group in groups or []:
        cases = [dict(item) for item in (group.get("cases") or []) if item]
        if not cases:
            continue
        item = dict(group)
        item["cases"] = cases
        cleaned.append(item)
    return sorted(
        cleaned,
        key=lambda g: (
            _number(g.get("floor")),
            _number(g.get("room")),
            normalize_space(g.get("judge_name")).lower(),
            normalize_space(g.get("court_name")).lower(),
        ),
    )


def _groups_from_live_rows(rows):
    """Rebuild cause-list groups from today's persistent live mirror."""
    grouped = {}
    for row in rows or []:
        key = (
            normalize_space(row.get("court_name")),
            normalize_space(row.get("judge_name")),
            normalize_space(row.get("floor")),
            normalize_space(row.get("room")),
        )
        group = grouped.setdefault(key, {
            "court_name": key[0],
            "judge_name": key[1],
            "floor": key[2],
            "room": key[3],
            "cases": [],
        })
        group["cases"].append({
            "case_number": row.get("case_number"),
            "case_title": row.get("case_title"),
            "stage": row.get("stage"),
            "previous_date": None,
        })
    return _usable_groups(grouped.values())

def _stage_bucket(stage):
    text = normalize_space(stage).lower()
    if any(word in text for word in ("bail", "appearance", "presence")):
        return "Appearance / Bail", "🔴"
    if any(word in text for word in ("arg", "consider", "consd", "order")):
        return "Arguments / Orders", "🟠"
    if any(word in text for word in ("evd", "evidence", "pws", "dws", "cross", "cevd", "pevi")):
        return "Evidence", "🟡"
    if any(word in text for word in ("reply", "filing", "record", "publication")):
        return "Reply / Filing", "🔵"
    return "Other", "⚪"


def _floor_label(group):
    floor = _number(group.get("floor"))
    court = normalize_space(group.get("court_name")) or "Court not recorded"
    if floor >= 80:
        return f"OUTSTATION • {court}"
    return f"FLOOR {normalize_space(group.get('floor')) or '?'}"


def build_summary(groups, display_date, source, live_count=None):
    groups = _usable_groups(groups)
    total = sum(len(group["cases"]) for group in groups)
    courts = len(groups)
    locations = Counter()
    stages = Counter()
    for group in groups:
        locations[_floor_label(group)] += len(group["cases"])
        for case in group["cases"]:
            bucket, _ = _stage_bucket(case.get("stage"))
            stages[bucket] += 1

    lines = [
        "🌅 MORNING COURT OPERATIONS",
        f"📅 {display_date}",
        f"⚖️ {total} matters  •  🏛 {courts} courts",
        f"🔗 Advocate Diaries: {source}",
        "",
        "🧭 COURT MOVEMENT ROUTE",
    ]
    for location, count in locations.items():
        icon = "🚗" if location.startswith("OUTSTATION") else "🏢"
        lines.append(f"{icon} {location.title()} — {count}")

    lines.extend(["", "🎯 TODAY'S STAGE MIX"])
    ordered = ("Appearance / Bail", "Arguments / Orders", "Evidence", "Reply / Filing", "Other")
    icons = {"Appearance / Bail": "🔴", "Arguments / Orders": "🟠", "Evidence": "🟡", "Reply / Filing": "🔵", "Other": "⚪"}
    for label in ordered:
        if stages[label]:
            lines.append(f"{icons[label]} {label}: {stages[label]}")

    if live_count is not None:
        lines.extend(["", f"🔴 LIVE BOARD READY: {live_count}/{total} matters"])
    lines.append("Tap a matter on the Live Board below to update its court status.")
    return "\n".join(lines)


def build_detail_chunks(groups, display_date):
    groups = _usable_groups(groups)
    blocks = []
    running = 1
    current_location = None
    for group in groups:
        location = _floor_label(group)
        if location != current_location:
            blocks.append(f"\n━━━━━━━━━━━━━━━━━━━━\n🏢 {location}\n━━━━━━━━━━━━━━━━━━━━")
            current_location = location

        court = normalize_space(group.get("court_name")) or "Court not recorded"
        judge = normalize_space(group.get("judge_name")) or "Judge not recorded"
        room = normalize_space(group.get("room")) or "?"
        lines = [f"🏛 Room {room} • {court}", f"👨‍⚖️ {judge}", f"📌 {len(group['cases'])} matter(s)", ""]
        for case in group["cases"]:
            stage = normalize_space(case.get("stage")) or "Not recorded"
            _, icon = _stage_bucket(stage)
            lines.extend([
                f"{running}. {icon} {normalize_space(case.get('case_number')) or 'Case number not recorded'}",
                f"   {normalize_space(case.get('case_title')) or 'Title not recorded'}",
                f"   📝 {stage}",
            ])
            previous = normalize_space(case.get("previous_date"))
            if previous:
                lines.append(f"   ⏮ {previous}")
            lines.append("")
            running += 1
        blocks.append("\n".join(lines).rstrip())

    header = f"⚖️ FLOOR-WISE CAUSE LIST\n📅 {display_date}"
    chunks = []
    current = header
    for block in blocks:
        candidate = current + "\n\n" + block
        if len(candidate) <= MESSAGE_LIMIT:
            current = candidate
        else:
            chunks.append(current)
            current = header + "\n\n" + block
    if current:
        chunks.append(current)
    return chunks


async def publish_morning_operations(context, force=False):
    group_id = os.getenv("OFFICE_GROUP_CHAT_ID")
    if not group_id:
        raise RuntimeError("OFFICE_GROUP_CHAT_ID is missing")

    now = datetime.now(IST)
    date_key = now.strftime("%Y-%m-%d")
    sent_key = "morning_operations_last_sent_date"
    if not force and context.application.bot_data.get(sent_key) == date_key:
        return "duplicate"

    groups, source = await asyncio.to_thread(fetch_advocate_diaries_cause_groups, now.date())
    groups = _usable_groups(groups)
    total = sum(len(group["cases"]) for group in groups)
    display_date = now.strftime("%d-%m-%Y | %A")

    live_count = None
    live_rows = []
    live_source = source
    try:
        from services.live_hearing_service import list_live_hearings, sync_live_hearings
        live_count, live_source = await asyncio.to_thread(sync_live_hearings, now.date())
        live_rows = await asyncio.to_thread(list_live_hearings, now.date())
    except Exception as exc:
        print(f"MORNING LIVE BOARD INITIALIZATION FAILED: {type(exc).__name__}: {exc}")

    if not groups and live_rows:
        groups = _groups_from_live_rows(live_rows)
        total = sum(len(group["cases"]) for group in groups)
        source = f"Live Hearing Mirror ({live_source})"
        live_count = len(live_rows)

    await context.bot.send_message(
        chat_id=int(group_id),
        text=build_summary(groups, display_date, source, live_count),
    )
    for chunk in build_detail_chunks(groups, display_date):
        await context.bot.send_message(chat_id=int(group_id), text=chunk)

    if live_rows:
        from commands.live_hearings import _board_keyboard, _board_text
        await context.bot.send_message(
            chat_id=int(group_id),
            text=_board_text(live_rows, live_source, 0),
            reply_markup=_board_keyboard(live_rows, 0),
        )
    elif total:
        await context.bot.send_message(
            chat_id=int(group_id),
            text="⚠️ Live Hearing board could not be initialized. Use /livehearings to retry.",
        )

    context.application.bot_data[sent_key] = date_key
    print(f"MORNING OPERATIONS SENT: date={date_key}, matters={total}, live={len(live_rows)}")
    return "sent"