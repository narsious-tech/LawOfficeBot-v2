"""Framework-independent Law Office role and access policy."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROLE_RANK = {"unlinked": -1, "staff": 0, "supervisor": 1, "admin": 2}

PUBLIC_COMMANDS = {
    "start", "office", "home", "menu", "command", "commands", "help",
    "mychatid", "linkstaff", "cancel", "cancelai", "cancelcommunication",
    "cancelloan", "none",
}

SUPERVISOR_COMMANDS = {
    "newcase", "pendingfees", "balance", "addpayment", "closecase", "addnote",
    "pendingtasks", "assignresponsibility", "workboard", "workcontrol",
    "reconcileassignments", "works", "work", "assignwork", "completework",
    "stafftasks", "assigntask", "reassigntask", "reassignhistory",
    "reopentask", "reopenhistory", "setpriority", "welcomeclient",
    "sendnewcase", "newcasewelcome", "newcaseinfo", "sendcasestatus",
    "pendingclientverification", "confirmclientdetails", "clientchanges",
    "messagehistory", "refreshofficeprofile", "missingmobiles",
    "missingmobilesreport", "mobileaudit", "mobileupdatequeue",
    "mobileupdatequeuesummary", "filesready", "eveningdashboard",
    "printablecauselist", "readiness", "morningreadiness", "officestatus",
    "attendancetoday", "staffattendance", "whoinoffice", "syncreport",
    "generatehearingreminders", "hearingqueue", "hearingpreview",
    "casecolumns", "casefolder", "addtimeline", "clientphone",
}

ADMIN_COMMANDS = {
    "synccases", "synccasesv2", "synccasesv3", "synctimeline", "repairmobiles",
    "syncattendancetoday", "linkedstaff", "delinkstaff", "approveattendance",
    "ai", "aicost", "loanledger", "deleteledger", "whatsappstatus",
    "testwhatsapp", "whatsappinbox", "retrywhatsapp", "ecourts",
    "syncecourts", "ecourtsmissing", "ecourtsreport", "ecourtsapprove",
    "ecourtsinspect", "ecourtschanges", "ecourtsreview", "ecourtsmatches",
    "ecourtsops", "ecourtsorders", "syncecourtsorders", "ecourtswork",
    "ecourtsdatecheck", "ejagriti", "ejagritilink", "ejagritiupdate",
    "ejagritireview", "ejagritiorder", "debugcasejson", "inspectadcase",
    "inspectadclient", "testad", "testweb", "testcausejob",
    "testpendingsummary", "testcompletedsummary", "testdeadlinealert",
    "testmanualdeadline", "teststafflogin", "teststaffbriefs",
    "teststaffclosing", "testadwebcreatecase", "testadrealcase",
    "testemailalerts", "emailalertstatus", "testforgotcheckout",
    "testattendancesummary", "testloanreminders", "explore",
}

ADMIN_CALLBACK_PREFIXES = ("ecr:", "ejg:", "loan:", "ajayai:")
SUPERVISOR_CALLBACK_PREFIXES = (
    "comm:", "efs:", "s13:works:all", "s13:finance:", "s13:staff:"
)
SUPERVISOR_CALLBACKS = {"los:status", "los:evening", "los:readiness"}
PUBLIC_CALLBACK_PREFIXES = ("cc:",)


@dataclass(frozen=True)
class AccessIdentity:
    level: str
    name: str
    role: str
    linked: bool


def configured_admin_ids() -> set[int]:
    # Chat/group IDs are deliberately excluded: authority belongs to senders.
    values = (os.getenv("ADMIN_USER_ID", ""), os.getenv("AI_ADMIN_USER_IDS", ""))
    result: set[int] = set()
    for value in values:
        for token in str(value).split(","):
            token = token.strip()
            if token.lstrip("-").isdigit():
                result.add(int(token))
    return result


def resolve_identity(user_id: int | None) -> AccessIdentity:
    if user_id is not None and int(user_id) in configured_admin_ids():
        return AccessIdentity("admin", "Ajay", "admin", True)
    if user_id is None:
        return AccessIdentity("unlinked", "Unknown", "unlinked", False)
    try:
        from services.role_intelligence_service import staff_profile

        profile = staff_profile(int(user_id))
        name = str(profile.get("staff_name") or "Staff").strip()
        role = str(profile.get("role") or "staff").strip().lower()
        active = bool(profile.get("is_active", True))
        linked = active and not (name.casefold() == "staff" and role == "staff")
        first_name = name.casefold().split(" ", 1)[0]
        if not linked:
            return AccessIdentity("unlinked", name, role, False)
        if role in {"admin", "owner", "principal"} or first_name == "ajay":
            level = "admin"
        elif role in {"supervisor", "manager", "senior"} or first_name == "priya":
            level = "supervisor"
        else:
            level = "staff"
        return AccessIdentity(level, name, role, True)
    except Exception:
        logger.exception("Central access profile lookup failed for user %s", user_id)
        return AccessIdentity("unlinked", "Staff", "unlinked", False)


def required_level_for_command(command: str) -> str:
    value = str(command or "").strip().lower().lstrip("/").split("@", 1)[0]
    if value in PUBLIC_COMMANDS:
        return "unlinked"
    if value in ADMIN_COMMANDS or value.startswith("test"):
        return "admin"
    if value in SUPERVISOR_COMMANDS:
        return "supervisor"
    return "staff"


def required_level_for_callback(data: str) -> str:
    value = str(data or "")
    if value.startswith(PUBLIC_CALLBACK_PREFIXES):
        return "unlinked"
    if value.startswith(ADMIN_CALLBACK_PREFIXES):
        return "admin"
    if value in SUPERVISOR_CALLBACKS or value.startswith(SUPERVISOR_CALLBACK_PREFIXES):
        return "supervisor"
    return "staff"


def can_complete_case_work(user_id: int, work_id: int) -> bool:
    """Allow linked staff to complete only a case Work assigned to them."""
    from config import DATABASE_URL
    import psycopg2

    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-access-control",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1
                FROM case_works w
                JOIN staff_accounts s
                  ON LOWER(TRIM(s.staff_name)) = LOWER(TRIM(w.assigned_to))
                WHERE w.id=%s
                  AND s.telegram_user_id=%s
                  AND s.is_active=TRUE
                  AND UPPER(COALESCE(w.status,'PENDING'))='PENDING'
                LIMIT 1
            """, (int(work_id), int(user_id)))
            return bool(cur.fetchone())
    finally:
        conn.close()
