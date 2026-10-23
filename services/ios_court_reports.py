"""Read-only adapters for the existing Telegram morning and court reports."""
from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def _step(stage, callback, *args, **kwargs):
    logger = logging.getLogger(__name__)
    logger.info('iOS court report step started: %s', stage)
    try:
        result = callback(*args, **kwargs)
    except Exception as exc:
        # Log only a fixed step label and exception class, never exception text.
        logger.error('iOS court report step failed: %s type=%s', stage, type(exc).__name__)
        raise
    logger.info('iOS court report step completed: %s', stage)
    return result


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
    groups, source = _step('cause_list_fetch', fetch_advocate_diaries_cause_groups, target)
    leave_checked = True
    warnings = []
    try:
        filtered_groups, excluded, _ = _step('confirmed_leave_read', apply_leave_exclusions,
                                            groups, target, read_only=True)
        groups = filtered_groups
    except Exception as exc:
        # This scope reads only the optional judge-leave tables. A missing table
        # must not be confused with an empty, successfully checked exclusion list.
        if getattr(exc, 'pgcode', None) != '42P01':
            raise
        leave_checked = False
        excluded = []
        warnings.append('Judge-leave verification unavailable: the optional leave tables are missing. '
                        'The list below is unfiltered; confirm judge leave in the office before acting.')
    # Use the bot's floor/room/judge formatter; omit optional task/client enrichment.
    cause_text, total = _step('cause_list_format', build_floor_wise_cause_list, groups, {}, {})
    lines = [
        'EVENING OPERATIONS DASHBOARD' if plan else "TODAY'S COURT CAUSE LIST",
        f'Court date: {target:%d-%m-%Y}',
    ]
    if plan:
        from services.staff_motivation_service import daily_quote
        lines.append(plan.heading)
        lines.extend(['Evening reflection:', daily_quote(today, 'evening')])
    if warnings:
        lines.extend(['', 'WARNING: ' + warnings[0], ''])
    lines.extend([
        f'Hearings: {total}',
        (f'Confirmed judge-leave exclusions: {len(excluded)}' if leave_checked
         else 'Judge-leave exclusions: NOT CHECKED'),
        f'Source: Advocate Diaries {source}',
        '',
        cause_text if total else ('No hearings are listed for this court date after confirmed judge-leave exclusions.'
                                 if leave_checked else 'Advocate Diaries lists no hearings for this court date. Judge leave was not checked.'),
    ])
    if plan:
        lines.extend(['', 'Select and assign physical files using the existing Telegram evening dashboard.'])
    return {
        'ok': True, 'report': kind, 'court_date': target.isoformat(),
        'timezone': 'Asia/Kolkata', 'source': source, 'hearings': total,
        'excluded_hearings': len(excluded) if leave_checked else None,
        'judge_leave_checked': leave_checked, 'warnings': warnings,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'summary': '\n'.join(lines),
    }


def build_morning_report():
    from commands.dashboard import build_morning_dashboard

    # The existing compact builder reports unavailable sources explicitly.
    text = _step('morning_build', build_morning_dashboard, read_only=True)
    text = text.replace('Select a module or quick action below.',
                        'Use the Shortcut menu to open another report.')
    return {
        'ok': True, 'report': 'morning', 'timezone': 'Asia/Kolkata',
        'report_date': datetime.now(IST).date().isoformat(),
        'generated_at': datetime.now(timezone.utc).isoformat(), 'summary': text,
    }
