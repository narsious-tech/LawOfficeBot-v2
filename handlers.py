"""
Telegram handler registration for LawOfficeBot-v2.

This module is a migration bridge for the current production bot. It removes
handler-registration code from the monolithic ``bot.py`` while allowing the
existing command functions and imported callbacks to remain where they are
until each feature is migrated into its final module.

Usage from bot.py:

    from handlers import register_handlers

    app = ApplicationBuilder().token(TOKEN).build()
    register_handlers(app, globals())

The namespace must contain the callbacks and conversation-state constants used
by the current production bot. Missing dependencies fail fast at startup with a
clear error instead of causing a silent Telegram command failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, TypeVar

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)


Callback = Callable[..., Any]
T = TypeVar("T")


class HandlerRegistrationError(RuntimeError):
    """Raised when a required handler dependency is unavailable."""


def _require(namespace: Mapping[str, Any], name: str) -> Any:
    """Return a required callback/state from the supplied bot namespace."""
    try:
        value = namespace[name]
    except KeyError as exc:
        raise HandlerRegistrationError(
            f"Cannot register Telegram handlers because {name!r} "
            "is missing from the bot namespace."
        ) from exc

    if value is None:
        raise HandlerRegistrationError(
            f"Cannot register Telegram handlers because {name!r} is None."
        )

    return value


def _command(
    application: Application,
    command_name: str,
    callback_name: str,
    namespace: Mapping[str, Any],
) -> None:
    """Register one Telegram command handler."""
    application.add_handler(
        CommandHandler(
            command_name,
            _require(namespace, callback_name),
        )
    )


def _build_upload_conversation(
    namespace: Mapping[str, Any],
) -> ConversationHandler:
    """Build the document-upload conversation."""
    waiting_file = _require(namespace, "WAITING_FILE")
    confirm_duplicate = _require(
        namespace,
        "CONFIRM_DUPLICATE_UPLOAD",
    )

    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "upload",
                _require(namespace, "upload_start"),
            )
        ],
        states={
            waiting_file: [
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    _require(namespace, "upload_file"),
                )
            ],
            confirm_duplicate: [
                CallbackQueryHandler(
                    _require(
                        namespace,
                        "duplicate_upload_callback",
                    ),
                    pattern=r"^duplicate_upload:",
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                _require(namespace, "cancel_upload"),
            )
        ],
        allow_reentry=True,
    )


def _build_new_case_conversation(
    namespace: Mapping[str, Any],
) -> ConversationHandler:
    """Build the existing new-case intake conversation."""
    text_input = filters.TEXT & ~filters.COMMAND

    state_callbacks = (
        ("CLIENT", "client"),
        ("MOBILE", "mobile"),
        ("ADVOCATEFOR", "advocate_for"),
        ("CLIENTTYPE", "client_type_input"),
        ("TITLEPETITIONER", "title_petitioner"),
        ("TITLERESPONDENT", "title_respondent"),
        ("CASETYPE", "case_type"),
        ("COURT", "court"),
        ("JUDGE", "judge"),
        ("OPPOSITE", "opposite"),
        ("HEARING", "hearing"),
        ("FEE", "fee"),
        ("ADVANCE", "advance"),
        ("CONFIRM", "confirm_newcase"),
    )

    states: dict[Any, list[MessageHandler]] = {}

    for state_name, callback_name in state_callbacks:
        states[_require(namespace, state_name)] = [
            MessageHandler(
                text_input,
                _require(namespace, callback_name),
            )
        ]

    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "newcase",
                _require(namespace, "newcase"),
            )
        ],
        states=states,
        fallbacks=[
            CommandHandler(
                "cancel",
                _require(namespace, "cancel"),
            ),
            CommandHandler(
                "findcase",
                _require(namespace, "findcase"),
            ),
        ],
    )


def register_handlers(
    application: Application,
    namespace: Mapping[str, Any],
) -> None:
    """
    Register all Telegram handlers used by the current production bot.

    Handler ordering is intentionally preserved because Telegram dispatch is
    order-sensitive, particularly for ConversationHandler and callback-query
    handlers.
    """

    # ------------------------------------------------------------------
    # Client communication
    # ------------------------------------------------------------------
    application.add_handler(
        _require(
            namespace,
            "build_communication_conversation_handler",
        )()
    )

    application.add_handler(
        CallbackQueryHandler(
            _require(namespace, "communication_callback"),
            pattern=r"^comm:",
        )
    )

    for command_name, callback_name in (
        ("missingmobiles", "missingmobiles"),
        (
            "pendingclientverification",
            "pendingclientverification",
        ),
        ("confirmclientdetails", "confirmclientdetails"),
        ("clientchanges", "clientchanges"),
        ("messagehistory", "messagehistory"),
        ("refreshofficeprofile", "refreshofficeprofile"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # Conversations must be registered before overlapping commands.
    # ------------------------------------------------------------------
    application.add_handler(
        _build_new_case_conversation(namespace)
    )

    # ------------------------------------------------------------------
    # Core case, hearing and utility commands
    # ------------------------------------------------------------------
    for command_name, callback_name in (
        ("testad", "test_ad"),
        ("start", "start"),
        ("findcase", "findcase"),
        ("synccases", "synccases"),
        ("case", "case"),
        ("pendingcases", "pendingcases"),
        ("todayhearings", "todayhearings"),
        ("tomorrowcause", "tomorrowcause"),
        ("explore", "explore"),
        ("attendance", "attendance"),
        ("approveattendance", "approve_attendance"),
        ("linkstaff", "linkstaff"),
        ("linkedstaff", "linkedstaff"),
        ("delinkstaff", "delinkstaff"),
        ("checkin", "checkin"),
        ("checkout", "checkout"),
        ("testweb", "test_web"),
        ("pendingfees", "pendingfees"),
        ("searchcase", "searchcase"),
        ("balance", "balance"),
        ("addpayment", "addpayment"),
        ("closecase", "closecase"),
        ("addnote", "addnote"),
        ("pendingtasks", "pendingtasks"),
        ("assignresponsibility", "assignresponsibility"),
        ("mychatid", "mychatid"),
        ("testcausejob", "test_cause_job"),
        ("works", "works"),
        ("work", "work"),
        ("completework", "completework"),
        ("assignwork", "assignwork"),
        ("mytasks", "mytasks"),
        ("stafftasks", "stafftasks"),
        ("completetask", "completetask"),
        ("testpendingsummary", "test_pending_summary"),
        ("assigntask", "assigntask"),
        ("teststafflogin", "teststafflogin"),
        (
            "testcompletedsummary",
            "test_completed_summary",
        ),
        ("testdeadlinealert", "test_deadline_alert"),
        (
            "testmanualdeadline",
            "test_manual_deadline_reminder",
        ),
        ("commands", "commands"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # File and Google Drive commands
    # ------------------------------------------------------------------
    application.add_handler(
        _build_upload_conversation(namespace)
    )

    for command_name, callback_name in (
        ("casefolder", "casefolder"),
        ("casefiles", "casefiles"),
        ("files", "files"),
        ("latestfiles", "latestfiles"),
        ("sharecasefolder", "sharecasefolder"),
        ("filehistory", "filehistory"),
        ("openfile", "openfile"),
        ("findfile", "findfile"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # Diagnostics and synchronization
    # ------------------------------------------------------------------
    for command_name, callback_name in (
        ("testadwebcreatecase", "test_ad_web_create_case"),
        ("testadrealcase", "test_ad_web_create_real_case"),
        ("debugcasejson", "debugcasejson"),
        ("syncreport", "syncreport"),
        ("attendanceapp", "attendanceapp"),
        (
            "syncattendancetoday",
            "sync_today_attendance_sessions",
        ),
        ("whoinoffice", "whoinoffice"),
        ("attendancetoday", "attendancetoday"),
        ("staffattendance", "staffattendance"),
        ("testforgotcheckout", "test_forgot_checkout"),
        (
            "testattendancesummary",
            "test_attendance_summary",
        ),
        ("taskdetails", "taskdetails"),
        ("taskhistory", "taskhistory"),
        ("morningdashboard", "morningdashboard"),
        ("teststaffbriefs", "test_staff_morning_briefs"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # Task callback buttons and task-administration commands
    # ------------------------------------------------------------------
    application.add_handler(
        CallbackQueryHandler(
            _require(namespace, "task_button_callback"),
            pattern=(
                r"^(taskdetails|completetask|"
                r"confirmcomplete|cancelcomplete):\d+$"
            ),
        )
    )

    for command_name, callback_name in (
        ("reassigntask", "reassign_task"),
        ("reassignhistory", "reassign_history"),
        ("reopentask", "reopen_task"),
        ("reopenhistory", "reopen_history"),
        ("setpriority", "set_task_priority"),
        ("casecolumns", "show_case_columns"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # Client timeline
    # ------------------------------------------------------------------
    for command_name, callback_name in (
        ("clienttimeline", "clienttimeline"),
        ("synctimeline", "synctimeline"),
        ("addtimeline", "addtimeline"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    # ------------------------------------------------------------------
    # Hearing automation
    # ------------------------------------------------------------------
    for command_name, callback_name in (
        (
            "generatehearingreminders",
            "generatehearingreminders",
        ),
        ("hearingqueue", "hearingqueue"),
        ("hearingpreview", "hearingpreview"),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )

    application.add_handler(
        CallbackQueryHandler(
            _require(
                namespace,
                "hearing_automation_callback",
            ),
            pattern=r"^hear:",
        )
    )

    # ------------------------------------------------------------------
    # Advocate Diaries v2/v3 and mobile audit
    # ------------------------------------------------------------------
    for command_name, callback_name in (
        ("synccasesv2", "synccasesv2"),
        ("inspectadcase", "inspectadcase"),
        ("inspectadclient", "inspectadclient"),
        ("synccasesv3", "synccasesv3"),
        ("missingmobilesreport", "missingmobilesreport"),
        ("repairmobiles", "repairmobiles"),
        ("mobileaudit", "mobileaudit"),
        ("mobileupdatequeue", "mobileupdatequeue"),
        (
            "mobileupdatequeuesummary",
            "mobileupdatequeuesummary",
        ),
    ):
        _command(
            application,
            command_name,
            callback_name,
            namespace,
        )
