from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from services.live_hearing_service import (
    get_live_hearing, list_live_hearings, set_live_hearing_status, sync_live_hearings,
)

IST = ZoneInfo("Asia/Kolkata")
STATUS_LABELS = {
    "LISTED": "⚪ Listed", "CALLED": "🟢 Called", "PASSED_OVER": "🟡 Passed Over",
    "ADJOURNED": "🔵 Adjourned", "ORDER_RESERVED": "🟣 Order Reserved", "DISPOSED": "✅ Disposed",
}


def _board_keyboard(rows):
    buttons = [[InlineKeyboardButton(f"#{r['id']} {r.get('case_number') or 'Open hearing'}", callback_data=f"lhc:open:{r['id']}")] for r in rows[:12]]
    buttons.append([InlineKeyboardButton("🔄 Sync & Refresh", callback_data="lhc:refresh")])
    return InlineKeyboardMarkup(buttons)


def _detail_keyboard(hearing_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Called", callback_data=f"lhc:set:{hearing_id}:CALLED"), InlineKeyboardButton("🟡 Passed Over", callback_data=f"lhc:set:{hearing_id}:PASSED_OVER")],
        [InlineKeyboardButton("🔵 Adjourned", callback_data=f"lhc:set:{hearing_id}:ADJOURNED"), InlineKeyboardButton("🟣 Reserved", callback_data=f"lhc:set:{hearing_id}:ORDER_RESERVED")],
        [InlineKeyboardButton("✅ Disposed", callback_data=f"lhc:set:{hearing_id}:DISPOSED"), InlineKeyboardButton("⚪ Reset Listed", callback_data=f"lhc:set:{hearing_id}:LISTED")],
        [InlineKeyboardButton("⬅️ Live Board", callback_data="lhc:board")],
    ])


def _board_text(rows, source=None):
    now = datetime.now(IST)
    lines = ["⚖️ LIVE HEARING CONTROL", f"📅 {now:%d-%m-%Y}  •  🕘 {now:%I:%M %p} IST"]
    if source: lines.append(f"🔗 Synced via Advocate Diaries {source}")
    lines.append("")
    if not rows:
        return "\n".join(lines + ["No hearings are listed for today."])
    counts = {}
    for r in rows: counts[r['status']] = counts.get(r['status'], 0) + 1
    lines += [f"📌 Total {len(rows)}   🟢 Called {counts.get('CALLED',0)}   🟡 Passed {counts.get('PASSED_OVER',0)}", f"✅ Closed {sum(counts.get(x,0) for x in ('ADJOURNED','ORDER_RESERVED','DISPOSED'))}", ""]
    for r in rows[:12]:
        location = " / ".join(x for x in [r.get('floor') and f"Floor {r['floor']}", r.get('room') and f"Room {r['room']}"] if x)
        lines += [f"{STATUS_LABELS.get(r['status'], r['status'])}  #{r['id']}", f"{r.get('case_number') or '-'} — {r.get('case_title') or '-'}", f"👨‍⚖️ {r.get('judge_name') or r.get('court_name') or '-'}" + (f" | {location}" if location else ""), ""]
    if len(rows) > 12: lines.append(f"Showing first 12 of {len(rows)} hearings.")
    return "\n".join(lines).strip()


def _detail_text(r):
    return "\n".join([
        "⚖️ LIVE HEARING", "",
        f"🔢 {r.get('case_number') or '-'}", f"📝 {r.get('case_title') or '-'}",
        f"📍 Stage: {r.get('stage') or '-'}", f"👨‍⚖️ {r.get('judge_name') or '-'}",
        f"🏛 {r.get('court_name') or '-'}", f"📌 Floor {r.get('floor') or '-'} | Room {r.get('room') or '-'}",
        f"📊 Status: {STATUS_LABELS.get(r.get('status'), r.get('status'))}", "", "Select the current hearing status.",
    ])


async def livehearings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        _, source = sync_live_hearings()
        rows = list_live_hearings()
        await update.effective_message.reply_text(_board_text(rows, source), reply_markup=_board_keyboard(rows))
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Live hearing board failed:\n{type(exc).__name__}: {exc}")


async def live_hearing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    await q.answer()
    parts = (q.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "board"
    try:
        if action in {"board", "refresh"}:
            source = None
            if action == "refresh": _, source = sync_live_hearings()
            rows = list_live_hearings()
            await q.edit_message_text(_board_text(rows, source), reply_markup=_board_keyboard(rows)); return
        if action == "open":
            row = get_live_hearing(int(parts[2]))
            if not row: await q.edit_message_text("Hearing not found."); return
            await q.edit_message_text(_detail_text(row), reply_markup=_detail_keyboard(row['id'])); return
        if action == "set":
            row = set_live_hearing_status(int(parts[2]), parts[3], q.from_user.id)
            if not row: await q.edit_message_text("Hearing not found."); return
            await q.edit_message_text(_detail_text(row), reply_markup=_detail_keyboard(row['id'])); return
    except Exception as exc:
        await q.edit_message_text(f"❌ Live hearing update failed:\n{type(exc).__name__}: {exc}")
