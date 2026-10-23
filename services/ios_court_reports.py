"""Read-only adapters for the existing Telegram morning and court reports."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def build_court_report(kind):
    from commands.dashboard import (
        build_floor_wise_cause_list,
        fetch_advocate_diaries_cause_groups,
    )
    from services.judge_leave_service import apply_leave_exclusions
    from services.office_calendar_service import manual_evening_plan

    today = datetime.now(IST).date()
    plan = manual_evening_plan(today) if kind == 'evening' else None
    target = plan.target_date if plan else today
    groups, source = fetch_advocate_diaries_cause_groups(target)
    groups, excluded, _ = apply_leave_exclusions(groups, target, read_only=True)
    # Use the bot's floor/room/judge formatter; omit optional task/client enrichment.
    cause_text, total = build_floor_wise_cause_list(groups, {}, {})
    lines = [
        'EVENING OPERATIONS DASHBOARD' if plan else "TODAY'S COURT CAUSE LIST",
        f'Court date: {target:%d-%m-%Y}',
    ]
    if plan:
        from services.staff_motivation_service import daily_quote
        lines.append(plan.heading)
        lines.extend(['Evening reflection:', daily_quote(today, 'evening')])
    lines.extend([
        f'Hearings: {total}',
        f'Confirmed judge-leave exclusions: {len(excluded)}',
        f'Source: Advocate Diaries {source}',
        '',
        cause_text if total else 'No hearings are listed for this court date after confirmed judge-leave exclusions.',
    ])
    if plan:
        lines.extend(['', 'Select and assign physical files using the existing Telegram evening dashboard.'])
    return {
        'ok': True, 'report': kind, 'court_date': target.isoformat(),
        'timezone': 'Asia/Kolkata', 'source': source, 'hearings': total,
        'excluded_hearings': len(excluded),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'summary': '\n'.join(lines),
    }


def build_morning_report():
    from commands.dashboard import build_morning_dashboard

    # The existing compact builder reports unavailable sources explicitly.
    text = build_morning_dashboard(read_only=True)
    text = text.replace('Select a module or quick action below.',
                        'Use the Shortcut menu to open another report.')
    return {
        'ok': True, 'report': 'morning', 'timezone': 'Asia/Kolkata',
        'report_date': datetime.now(IST).date().isoformat(),
        'generated_at': datetime.now(timezone.utc).isoformat(), 'summary': text,
    }
