from pathlib import Path
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected code block not found in {path}. Your deployed file may be a different version.")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"UPDATED: {path}")

evening = ROOT / "commands" / "evening_dashboard.py"
live = ROOT / "commands" / "live_hearings.py"

old_evening = '''    for idx in range(start, end):
        symbol = "✅" if idx in selected else "⬜"
        number = cases[idx]["case_number"]
        label = f"{symbol} {idx + 1}. {number}"[:55]
        rows.append([InlineKeyboardButton(label, callback_data=f"efs:{target.isoformat()}:t:{idx}:{page}")])
'''
new_evening = '''    for idx in range(start, end):
        symbol = "✅" if idx in selected else "⬜"
        case = cases[idx]
        title = str(case.get("case_title") or "").strip()
        number = str(case.get("case_number") or "").strip()
        if not title or title == "Title not recorded":
            title = number or "Case details not recorded"
        label = f"{symbol} {idx + 1}. {title}"[:60]
        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=f"efs:{target.isoformat()}:t:{idx}:{page}",
            )
        ])
'''
replace_once(evening, old_evening, new_evening)

old_evening_text = '''        lines.extend([
            f"{mark} {idx + 1}. {case['case_number']}",
            f"   {case['case_title']}",
            f"   {case['court']} | Floor {case['floor']} | Room {case['room']}",
            f"   Purpose: {case['purpose']}",
            "",
        ])
'''
new_evening_text = '''        lines.extend([
            f"{mark} {idx + 1}. {case['case_title']}",
            f"   Case No.: {case['case_number']}",
            f"   {case['court']} | Floor {case['floor']} | Room {case['room']}",
            f"   Purpose: {case['purpose']}",
            "",
        ])
'''
replace_once(evening, old_evening_text, new_evening_text)

old_live = '''    buttons = [[InlineKeyboardButton(
        f"{STATUS_LABELS.get(r.get('status'), '⚪')} #{r['id']} {r.get('case_number') or 'Open'}",
        callback_data=f"lhc:open:{r['id']}:{page}",
    )] for r in visible]
'''
new_live = '''    buttons = []
    for r in visible:
        title = str(r.get("case_title") or "").strip()
        number = str(r.get("case_number") or "").strip()
        matter = title or number or "Open hearing"
        label = f"{STATUS_LABELS.get(r.get('status'), '⚪')} {matter}"[:60]
        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"lhc:open:{r['id']}:{page}",
            )
        ])
'''
replace_once(live, old_live, new_live)

print()
print("SUCCESS")
print("Evening Dashboard: checkbox buttons now use case titles.")
print("Live Hearing: open-hearing buttons now use case titles.")
print("Existing callbacks, database logic, file assignments and hearing completion logic are unchanged.")
