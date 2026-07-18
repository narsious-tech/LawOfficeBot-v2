"""
Central scheduler for LawOfficeBot-v2.

This module is responsible for registering all background jobs with the
python-telegram-bot JobQueue.

During the migration from the monolithic bot.py, this file acts as a bridge:
the actual job callback functions still live in bot.py until they are moved
into the jobs/ package.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from telegram.ext import Application


class SchedulerRegistrationError(RuntimeError):
    """Raised when a required scheduled job is missing."""


def _require(namespace: Mapping[str, Any], name: str):
    try:
        return namespace[name]
    except KeyError as exc:
        raise SchedulerRegistrationError(
            f"Scheduled job '{name}' is missing."
        ) from exc


def register_scheduler(
    application: Application,
    namespace: Mapping[str, Any],
) -> None:
    """
    Register all recurring jobs.

    Usage
    -----

    from scheduler import register_scheduler

    register_scheduler(app, globals())
    """

    jq = application.job_queue

    # ---------------------------------------------------------
    # Hearing automation
    # ---------------------------------------------------------

    if "generate_hearing_reminders_job" in namespace:
        jq.run_daily(
            _require(namespace, "generate_hearing_reminders_job"),
            time=_require(namespace, "HEARING_JOB_TIME"),
            name="hearing_reminders",
        )

    # ---------------------------------------------------------
    # Morning dashboard
    # ---------------------------------------------------------

    if "morning_dashboard_job" in namespace:
        jq.run_daily(
            _require(namespace, "morning_dashboard_job"),
            time=_require(namespace, "MORNING_DASHBOARD_TIME"),
            name="morning_dashboard",
        )

    # ---------------------------------------------------------
    # Attendance
    # ---------------------------------------------------------

    if "attendance_summary_job" in namespace:
        jq.run_daily(
            _require(namespace, "attendance_summary_job"),
            time=_require(namespace, "ATTENDANCE_SUMMARY_TIME"),
            name="attendance_summary",
        )

    if "forgot_checkout_job" in namespace:
        jq.run_repeating(
            _require(namespace, "forgot_checkout_job"),
            interval=300,
            first=60,
            name="forgot_checkout",
        )

    # ---------------------------------------------------------
    # Deadlines
    # ---------------------------------------------------------

    if "deadline_alert_job" in namespace:
        jq.run_hourly(
            _require(namespace, "deadline_alert_job"),
            name="deadline_alerts",
        )

    # ---------------------------------------------------------
    # Cause list
    # ---------------------------------------------------------

    if "cause_list_job" in namespace:
        jq.run_daily(
            _require(namespace, "cause_list_job"),
            time=_require(namespace, "CAUSE_LIST_TIME"),
            name="cause_list",
        )

    # ---------------------------------------------------------
    # Staff brief
    # ---------------------------------------------------------

    if "staff_brief_job" in namespace:
        jq.run_daily(
            _require(namespace, "staff_brief_job"),
            time=_require(namespace, "STAFF_BRIEF_TIME"),
            name="staff_brief",
        )

    # ---------------------------------------------------------
    # Mobile update queue
    # ---------------------------------------------------------

    if "mobile_update_queue_job" in namespace:
        jq.run_repeating(
            _require(namespace, "mobile_update_queue_job"),
            interval=900,
            first=120,
            name="mobile_update_queue",
        )
