import re
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

from config import DATABASE_URL


INDIA_COUNTRY_CODE = "91"


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def normalize_case_value(value: str) -> str:
    return (value or "").strip()


def normalize_mobile(value: str) -> str:
    """
    Return an Indian mobile number in WhatsApp international format.

    Examples:
        9876543210      -> 919876543210
        +919876543210   -> 919876543210
        00919876543210  -> 919876543210
    """
    digits = re.sub(
        r"\D",
        "",
        str(value or "")
    )

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = INDIA_COUNTRY_CODE + digits

    if len(digits) != 12:
        raise ValueError(
            "Enter a valid 10-digit Indian mobile number "
            "or a number beginning with +91."
        )

    if not digits.startswith(INDIA_COUNTRY_CODE):
        raise ValueError(
            "Only Indian WhatsApp numbers are supported in this version."
        )

    return digits


def display_mobile(value: str) -> str:
    digits = normalize_mobile(value)

    return (
        f"+{digits[:2]} "
        f"{digits[2:7]} "
        f"{digits[7:]}"
    )


def format_contact_number(value: str) -> str:
    """
    Format Indian mobile or landline numbers for client-facing messages.
    """
    digits = re.sub(
        r"\D",
        "",
        str(value or "")
    )

    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        digits = "91" + digits

    if len(digits) == 12 and digits.startswith("91"):
        national = digits[2:]

        if national.startswith(("6", "7", "8", "9")):
            return (
                f"+91 {national[:5]} "
                f"{national[5:]}"
            )

        if national.startswith("161") and len(national) == 10:
            return (
                f"+91 161 {national[3:6]} "
                f"{national[6:]}"
            )

        return (
            f"+91 {national[:3]} "
            f"{national[3:6]} "
            f"{national[6:]}"
        )

    return (value or "").strip()


def format_date(value: Any) -> str:
    if not value:
        return "Not presently available"

    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")

    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y")

    text = str(value).strip()

    for pattern in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(
                text,
                pattern
            ).strftime("%d-%m-%Y")

        except ValueError:
            continue

    return text


def table_columns(
    cur,
    table_name: str
) -> set:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
    """, (
        table_name,
    ))

    return {
        row[0]
        for row in cur.fetchall()
    }


def get_case_record(
    cur,
    case_value: str
) -> Optional[Dict[str, Any]]:
    cur.execute("""
        SELECT
            id,
            case_id,
            case_number,
            case_title,
            client_name,
            mobile,
            case_type,
            court_name,
            opposite_party,
            COALESCE(
                next_hearing,
                hearing_date
            ) AS next_hearing_date,
            status,
            judge_name,
            client_id,
            ad_client_id,
            client_verification_status,
            client_verification_sent_at,
            client_verified_at,
            client_correction_note
        FROM cases
        WHERE
            LOWER(TRIM(COALESCE(case_id, '')))
                = LOWER(TRIM(%s))
            OR
            LOWER(TRIM(COALESCE(case_number, '')))
                = LOWER(TRIM(%s))
        ORDER BY id DESC
        LIMIT 1
    """, (
        case_value,
        case_value
    ))

    row = cur.fetchone()

    if not row:
        return None

    keys = [
        "id",
        "case_id",
        "case_number",
        "case_title",
        "client_name",
        "mobile",
        "case_type",
        "court_name",
        "opposite_party",
        "next_hearing",
        "status",
        "judge_name",
        "client_id",
        "ad_client_id",
        "verification_status",
        "verification_sent_at",
        "verified_at",
        "correction_note",
    ]

    data = dict(
        zip(
            keys,
            row
        )
    )

    data["canonical_case_id"] = (
        data["case_number"]
        or data["case_id"]
        or case_value
    )

    return data


def get_client_record(
    cur,
    case: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    client_columns = table_columns(
        cur,
        "clients"
    )

    if not client_columns:
        return None

    select_fields = [
        "id",
        "client_name",
        "mobile",
    ]

    optional_fields = [
        "whatsapp_number",
        "email",
        "address",
        "verification_status",
        "verified_at",
        "correction_note",
        "ad_client_id",
    ]

    for field in optional_fields:
        if field in client_columns:
            select_fields.append(field)

    where_parts = []
    values = []

    if (
        case.get("client_id")
        and "id" in client_columns
    ):
        where_parts.append(
            "id = %s"
        )
        values.append(
            case["client_id"]
        )

    if (
        case.get("ad_client_id")
        and "ad_client_id" in client_columns
    ):
        where_parts.append(
            "ad_client_id = %s"
        )
        values.append(
            case["ad_client_id"]
        )

    if not where_parts:
        mobile_value = (
            case.get("mobile")
            or ""
        )

        if (
            mobile_value
            and "mobile" in client_columns
        ):
            digits = re.sub(
                r"\D",
                "",
                mobile_value
            )

            if digits:
                where_parts.append(
                    "REGEXP_REPLACE("
                    "COALESCE(mobile, ''), "
                    "'[^0-9]', '', 'g'"
                    ") = %s"
                )
                values.append(
                    digits[-10:]
                )

    if not where_parts:
        return None

    query = (
        "SELECT "
        + ", ".join(select_fields)
        + " FROM clients WHERE "
        + " OR ".join(where_parts)
        + " ORDER BY id DESC LIMIT 1"
    )

    cur.execute(
        query,
        tuple(values)
    )

    row = cur.fetchone()

    if not row:
        return None

    return dict(
        zip(
            select_fields,
            row
        )
    )


def resolve_client_mobile(
    case: Dict[str, Any],
    client: Optional[Dict[str, Any]]
) -> Optional[str]:
    candidates = []

    if client:
        candidates.extend([
            client.get("whatsapp_number"),
            client.get("mobile"),
        ])

    candidates.append(
        case.get("mobile")
    )

    for candidate in candidates:
        if not candidate:
            continue

        try:
            return normalize_mobile(
                str(candidate)
            )

        except ValueError:
            continue

    return None


def get_office_profile(
    cur
) -> Dict[str, str]:
    columns = table_columns(
        cur,
        "office_profile"
    )

    if not columns:
        return {
            "office_name": (
                "Law Office of Ajay Chawla"
            ),
            "office_whatsapp": "",
            "office_phone": "",
            "office_email": "",
            "office_hours": "",
            "court_office_address": (
                "District Courts, Ludhiana"
            ),
        }

    wanted_fields = [
        "office_name",
        "office_whatsapp",
        "office_phone",
        "office_email",
        "office_hours",
        "court_office_address",
        "evening_office_address",
        "website",
        "court_maps_link",
        "evening_maps_link",
    ]

    select_fields = [
        field
        for field in wanted_fields
        if field in columns
    ]

    if not select_fields:
        return {
            "office_name": (
                "Law Office of Ajay Chawla"
            )
        }

    order_clause = (
        " ORDER BY id DESC"
        if "id" in columns
        else ""
    )

    where_clause = (
        " WHERE is_active = TRUE"
        if "is_active" in columns
        else ""
    )

    cur.execute(
        "SELECT "
        + ", ".join(select_fields)
        + " FROM office_profile"
        + where_clause
        + order_clause
        + " LIMIT 1"
    )

    row = cur.fetchone()

    if not row:
        return {
            "office_name": (
                "Law Office of Ajay Chawla"
            )
        }

    profile = dict(
        zip(
            select_fields,
            row
        )
    )

    return {
        key: (
            str(value).strip()
            if value is not None
            else ""
        )
        for key, value in profile.items()
    }


def make_communication_ref() -> str:
    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    suffix = secrets.token_hex(2).upper()

    return (
        f"COM-{timestamp}-{suffix}"
    )


def client_is_verified(
    case: Dict[str, Any],
    client: Optional[Dict[str, Any]]
) -> bool:
    values = [
        case.get("verification_status"),
        (
            client.get("verification_status")
            if client
            else None
        ),
    ]

    for value in values:
        if (
            str(value or "")
            .strip()
            .upper()
            in {
                "CONFIRMED",
                "VERIFIED",
                "DETAILS_CONFIRMED",
            }
        ):
            return True

    if case.get("verified_at"):
        return True

    if (
        client
        and client.get("verified_at")
    ):
        return True

    return False


def client_case_count(
    cur,
    case: Dict[str, Any],
    mobile: Optional[str]
) -> int:
    conditions = []
    values = []

    if case.get("client_id"):
        conditions.append(
            "client_id = %s"
        )
        values.append(
            case["client_id"]
        )

    if case.get("ad_client_id"):
        conditions.append(
            "ad_client_id = %s"
        )
        values.append(
            case["ad_client_id"]
        )

    if mobile:
        conditions.append("""
            RIGHT(
                REGEXP_REPLACE(
                    COALESCE(mobile, ''),
                    '[^0-9]',
                    '',
                    'g'
                ),
                10
            ) = %s
        """)
        values.append(
            mobile[-10:]
        )

    if not conditions:
        return 1

    cur.execute(
        "SELECT COUNT(*) "
        "FROM cases WHERE "
        + " OR ".join(conditions),
        tuple(values)
    )

    return int(
        cur.fetchone()[0]
    )





def get_client_cases(
    cur,
    case: Dict[str, Any],
    mobile: Optional[str],
    exclude_current: bool = True
) -> List[Dict[str, Any]]:
    """
    Return all cases belonging to the same client.

    Matching priority:
    1. local client_id
    2. Advocate Diaries client ID
    3. normalized mobile number

    Results are sorted by upcoming hearing first.
    """
    conditions = []
    values = []

    if case.get("client_id"):
        conditions.append(
            "client_id = %s"
        )
        values.append(
            case["client_id"]
        )

    if case.get("ad_client_id"):
        conditions.append(
            "ad_client_id = %s"
        )
        values.append(
            case["ad_client_id"]
        )

    if mobile:
        conditions.append("""
            RIGHT(
                REGEXP_REPLACE(
                    COALESCE(mobile, ''),
                    '[^0-9]',
                    '',
                    'g'
                ),
                10
            ) = %s
        """)
        values.append(
            mobile[-10:]
        )

    if not conditions:
        return []

    where_parts = [
        "(" + " OR ".join(conditions) + ")"
    ]

    if exclude_current and case.get("id"):
        where_parts.append(
            "id <> %s"
        )
        values.append(
            case["id"]
        )

    cur.execute(
        """
        SELECT
            id,
            COALESCE(
                NULLIF(TRIM(case_number), ''),
                NULLIF(TRIM(case_id), ''),
                'Case number not recorded'
            ) AS case_reference,
            case_title,
            case_type,
            court_name,
            judge_name,
            COALESCE(
                next_hearing,
                hearing_date
            ) AS next_hearing_date,
            status
        FROM cases
        WHERE
        """
        + " AND ".join(where_parts)
        + """
        ORDER BY
            CASE
                WHEN COALESCE(
                    next_hearing,
                    hearing_date
                ) IS NULL
                THEN 1
                ELSE 0
            END,
            COALESCE(
                next_hearing,
                hearing_date
            ) ASC NULLS LAST,
            id DESC
        """,
        tuple(values)
    )

    rows = cur.fetchall()

    keys = [
        "id",
        "case_reference",
        "case_title",
        "case_type",
        "court_name",
        "judge_name",
        "next_hearing",
        "status",
    ]

    return [
        dict(zip(keys, row))
        for row in rows
    ]


def case_is_closed(case_item: Dict[str, Any]) -> bool:
    status = str(
        case_item.get("status")
        or ""
    ).strip().upper()

    closed_values = {
        "CLOSED",
        "DISPOSED",
        "DECIDED",
        "COMPLETED",
        "ARCHIVED",
        "WITHDRAWN",
        "DISMISSED",
        "SETTLED",
    }

    return status in closed_values


def build_existing_cases_summary(
    existing_cases: List[Dict[str, Any]],
    maximum_items: int = 5,
    include_current_matter: bool = True
) -> str:
    """
    Build a WhatsApp-friendly summary of the client's other matters.

    The listed matters exclude the newly registered case, but the relationship
    totals include it when include_current_matter=True.
    """
    if not existing_cases:
        return (
            "This is your first registered matter "
            "with our office."
        )

    active_cases = [
        item
        for item in existing_cases
        if not case_is_closed(item)
    ]

    closed_cases = [
        item
        for item in existing_cases
        if case_is_closed(item)
    ]

    current_count = (
        1
        if include_current_matter
        else 0
    )

    total_matters = (
        len(existing_cases)
        + current_count
    )

    total_active = (
        len(active_cases)
        + current_count
    )

    display_cases = (
        active_cases + closed_cases
    )[:maximum_items]

    lines = [
        "📂 YOUR RELATIONSHIP WITH OUR OFFICE",
        "",
        f"📊 Total Matters: {total_matters}",
        f"✅ Active Matters: {total_active}",
        f"📁 Closed / Disposed Matters: {len(closed_cases)}",
        "",
        (
            "Apart from the newly registered matter above, "
            "the following matters are linked with your "
            "client profile."
        ),
        "",
        (
            "They are listed in order of their upcoming "
            "hearing dates."
        ),
        "",
    ]

    for index, item in enumerate(
        display_cases,
        start=1
    ):
        lines.append(
            f"{index}. "
            f"{item.get('case_reference') or '-'}"
        )

        if item.get("case_type"):
            lines.append(
                f"   📄 {item['case_type']}"
            )

        if item.get("case_title"):
            lines.append(
                f"   ⚖️ {item['case_title']}"
            )

        if item.get("next_hearing"):
            lines.append(
                "   📅 Next Hearing: "
                f"{format_date(item['next_hearing'])}"
            )

        if item.get("status"):
            lines.append(
                f"   📌 Status: "
                f"{item['status']}"
            )

        lines.append("")

    remaining = (
        len(existing_cases)
        - len(display_cases)
    )

    if remaining > 0:
        lines.append(
            f"...and {remaining} more matter(s)."
        )
        lines.append("")

    lines.append(
        "You will continue to receive hearing dates, "
        "document requests and important updates for "
        "all your matters on this WhatsApp number."
    )

    return "\n".join(lines)



def infer_client_role(
    case: Dict[str, Any]
) -> str:
    client_name = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(
            case.get("client_name")
            or ""
        ).lower()
    ).strip()

    title = str(
        case.get("case_title")
        or ""
    )

    parts = re.split(
        r"\bvs\.?\b|\bversus\b|\bv\/s\b",
        title,
        maxsplit=1,
        flags=re.IGNORECASE
    )

    if (
        not client_name
        or len(parts) != 2
    ):
        return ""

    left = re.sub(
        r"[^a-z0-9]+",
        " ",
        parts[0].lower()
    ).strip()

    right = re.sub(
        r"[^a-z0-9]+",
        " ",
        parts[1].lower()
    ).strip()

    if client_name in left or left in client_name:
        return "Petitioner / Plaintiff / Complainant"

    if client_name in right or right in client_name:
        return "Respondent / Defendant / Accused"

    return ""


def office_signature(
    profile: Dict[str, str]
) -> str:
    office_name = (
        profile.get("office_name")
        or "Law Office of Ajay Chawla"
    )

    if not office_name.lower().endswith("advocates"):
        branded_name = f"{office_name}, Advocates"
    else:
        branded_name = office_name

    lines = [
        "Regards,",
        "",
        branded_name,
    ]

    whatsapp = profile.get("office_whatsapp") or ""
    phone = profile.get("office_phone") or ""
    email = profile.get("office_email") or ""
    court_address = profile.get("court_office_address") or ""
    evening_address = profile.get("evening_office_address") or ""
    hours = profile.get("office_hours") or ""
    court_map = profile.get("court_maps_link") or ""
    evening_map = profile.get("evening_maps_link") or ""

    if whatsapp:
        lines.append(
            f"📱 WhatsApp: {format_contact_number(whatsapp)}"
        )

    if phone:
        lines.append(
            f"☎️ Office: {format_contact_number(phone)}"
        )

    if email:
        lines.append(
            f"✉️ Email: {email}"
        )

    if court_address:
        lines.extend([
            "",
            "📍 Court Chamber",
            court_address,
        ])

    if court_map:
        lines.append(
            f"🗺 Location: {court_map}"
        )

    if evening_address:
        lines.extend([
            "",
            "📍 Evening Office",
            evening_address,
        ])

    if evening_map:
        lines.append(
            f"🗺 Location: {evening_map}"
        )

    if hours:
        lines.extend([
            "",
            "🕒 Office Hours",
            hours,
        ])

    lines.extend([
        "",
        (
            "For any query, clarification, "
            "document requirement or appointment, "
            "please contact the office using the "
            "details stated above."
        ),
        "",
        (
            "Messages received outside office hours "
            "will be attended to on the next working day."
        ),
    ])

    return "\n".join(lines)


def case_details_block(
    case: Dict[str, Any],
    resolved_mobile: Optional[str] = None
) -> str:
    role = infer_client_role(
        case
    )

    mobile = (
        resolved_mobile
        or case.get("resolved_mobile")
        or case.get("mobile")
        or ""
    )

    try:
        mobile_display = display_mobile(
            mobile
        )
    except ValueError:
        mobile_display = (
            format_contact_number(
                mobile
            )
            or "-"
        )

    lines = [
        f"👤 Client: {case.get('client_name') or '-'}",
        f"📱 Mobile: {mobile_display}",
    ]

    if role:
        lines.append(
            f"🧑‍⚖️ Your Role: {role}"
        )

    lines.extend([
        f"⚖️ Case Number: "
        f"{case.get('canonical_case_id') or '-'}",
        f"📄 Case Type: "
        f"{case.get('case_type') or '-'}",
        f"📋 Case Title: "
        f"{case.get('case_title') or '-'}",
        f"🏛 Court: "
        f"{case.get('court_name') or '-'}",
        f"👨‍⚖️ Presiding Officer: "
        f"{case.get('judge_name') or '-'}",
        f"👥 Opposite Party: "
        f"{case.get('opposite_party') or '-'}",
        f"📅 Next Hearing: "
        f"{format_date(case.get('next_hearing'))}",
    ])

    return "\n".join(
        lines
    )


def build_welcome_message(
    case: Dict[str, Any],
    profile: Dict[str, str],
    communication_ref: str,
    resolved_mobile: Optional[str] = None
) -> str:
    office_name = (
        profile.get("office_name")
        or "Law Office of Ajay Chawla"
    )

    if not office_name.lower().endswith("advocates"):
        branded_name = f"{office_name}, Advocates"
    else:
        branded_name = office_name

    return (
        f"Dear {case.get('client_name') or 'Client'},\n\n"
        f"Welcome to {branded_name}.\n\n"
        "Thank you for placing your trust in us. "
        "Your matter has been successfully entered "
        "into our office management system.\n\n"
        "YOUR CASE DETAILS\n\n"
        f"{case_details_block(case, resolved_mobile)}\n\n"
        "Please verify the above information carefully.\n\n"
        "Reply with:\n\n"
        "✅ DETAILS CORRECT\n\n"
        "or\n\n"
        "✏️ CHANGE REQUIRED\n\n"
        "If any information is incorrect, kindly mention "
        "the required correction in your reply.\n\n"
        "We will use this WhatsApp number to send hearing "
        "dates, important case updates, document requests "
        "and other communications relating to your matter.\n\n"
        "If any identity proof, photographs or other "
        "documents are still pending, kindly send them "
        "on this WhatsApp number.\n\n"
        f"{office_signature(profile)}"
    )


def build_new_case_message(
    case: Dict[str, Any],
    profile: Dict[str, str],
    communication_ref: str,
    existing_cases: Optional[
        List[Dict[str, Any]]
    ] = None,
    resolved_mobile: Optional[str] = None
) -> str:
    office_name = (
        profile.get("office_name")
        or "Law Office of Ajay Chawla"
    )

    if not office_name.lower().endswith("advocates"):
        branded_name = f"{office_name}, Advocates"
    else:
        branded_name = office_name

    existing_cases_text = (
        build_existing_cases_summary(
            existing_cases or []
        )
    )

    return (
        f"Dear {case.get('client_name') or 'Client'},\n\n"
        "Welcome back.\n\n"
        "Thank you for continuing to place your trust in "
        f"{branded_name}.\n\n"
        "Your new matter has been successfully registered "
        "in our office records and linked with your "
        "existing client profile.\n\n"
        f"⚖️ Case Number: "
        f"{case.get('canonical_case_id') or '-'}\n\n"
        "Kindly quote the above case number whenever you "
        "contact our office regarding this matter.\n\n"
        "📋 YOUR NEW CASE DETAILS\n\n"
        f"{case_details_block(case, resolved_mobile)}\n\n"
        f"{existing_cases_text}\n\n"
        "If your mobile number, address, email address or "
        "any other contact details have changed, kindly "
        "inform us so that our office records remain "
        "updated.\n\n"
        f"{office_signature(profile)}\n\n"
        "For quicker assistance, please mention your case "
        "number whenever you call, WhatsApp or visit our "
        "office."
    )



def build_case_status_message(
    case: Dict[str, Any],
    profile: Dict[str, str],
    communication_ref: str,
    resolved_mobile: Optional[str] = None
) -> str:
    return (
        f"Dear {case.get('client_name') or 'Client'},\n\n"
        "CASE STATUS UPDATE\n\n"
        f"⚖️ Case Number: "
        f"{case.get('canonical_case_id') or '-'}\n"
        f"📋 Case Title: "
        f"{case.get('case_title') or '-'}\n"
        f"🏛 Court: "
        f"{case.get('court_name') or '-'}\n"
        f"👨‍⚖️ Presiding Officer: "
        f"{case.get('judge_name') or '-'}\n"
        f"📅 Next Hearing: "
        f"{format_date(case.get('next_hearing'))}\n"
        f"📝 Current Status: "
        f"{case.get('status') or 'Under office review'}\n\n"
        "Please contact the office if you require any "
        "clarification regarding this update.\n\n"
        f"{office_signature(profile)}"
    )


def save_mobile_for_case_and_client(
    cur,
    case: Dict[str, Any],
    mobile: str
):
    normalized = normalize_mobile(
        mobile
    )

    cur.execute("""
        UPDATE cases
        SET mobile = %s
        WHERE id = %s
    """, (
        normalized,
        case["id"]
    ))

    client_columns = table_columns(
        cur,
        "clients"
    )

    if not client_columns:
        return normalized

    update_fields = []

    if "mobile" in client_columns:
        update_fields.append(
            "mobile = %s"
        )

    if "whatsapp_number" in client_columns:
        update_fields.append(
            "whatsapp_number = %s"
        )

    if not update_fields:
        return normalized

    values = [
        normalized
        for _ in update_fields
    ]

    where_parts = []
    where_values = []

    if case.get("client_id"):
        where_parts.append(
            "id = %s"
        )
        where_values.append(
            case["client_id"]
        )

    if (
        case.get("ad_client_id")
        and "ad_client_id" in client_columns
    ):
        where_parts.append(
            "ad_client_id = %s"
        )
        where_values.append(
            case["ad_client_id"]
        )

    if where_parts:
        cur.execute(
            "UPDATE clients SET "
            + ", ".join(update_fields)
            + " WHERE "
            + " OR ".join(where_parts),
            tuple(
                values
                + where_values
            )
        )

    return normalized


def create_message_log(
    cur,
    *,
    case: Dict[str, Any],
    client: Optional[Dict[str, Any]],
    phone_number: str,
    message_type: str,
    message_text: str,
    sent_by: int,
    communication_ref: str,
    template_name: str
) -> int:
    columns = table_columns(
        cur,
        "client_messages"
    )

    if not columns:
        raise RuntimeError(
            "client_messages table does not exist."
        )

    values_map = {
        "case_id": case.get(
            "canonical_case_id"
        ),
        "client_name": case.get(
            "client_name"
        ),
        "phone_number": phone_number,
        "channel": "WHATSAPP",
        "message_type": message_type,
        "message_text": message_text,
        "sent_by": sent_by,
        "delivery_status": "DRAFT",
        "created_at": datetime.now(),
        "client_id": (
            case.get("client_id")
            or (
                client.get("id")
                if client
                else None
            )
        ),
        "ad_client_id": case.get(
            "ad_client_id"
        ),
        "communication_ref": communication_ref,
        "template_name": template_name,
        "related_case_id": case.get(
            "canonical_case_id"
        ),
        "reply_status": "PENDING",
    }

    insert_columns = [
        key
        for key in values_map
        if key in columns
    ]

    if not insert_columns:
        raise RuntimeError(
            "client_messages table has no supported columns."
        )

    placeholders = ", ".join(
        ["%s"] * len(insert_columns)
    )

    cur.execute(
        "INSERT INTO client_messages ("
        + ", ".join(insert_columns)
        + ") VALUES ("
        + placeholders
        + ") RETURNING id",
        tuple(
            values_map[column]
            for column in insert_columns
        )
    )

    return int(
        cur.fetchone()[0]
    )


def update_message_status(
    cur,
    *,
    message_id: int,
    status: str
) -> bool:
    columns = table_columns(
        cur,
        "client_messages"
    )

    assignments = []
    values = []

    if "delivery_status" in columns:
        assignments.append(
            "delivery_status = %s"
        )
        values.append(
            status
        )

    if (
        status == "SENT_MANUALLY"
        and "sent_at" in columns
    ):
        assignments.append(
            "sent_at = CURRENT_TIMESTAMP"
        )

    if not assignments:
        return False

    values.append(
        message_id
    )

    cur.execute(
        "UPDATE client_messages SET "
        + ", ".join(assignments)
        + " WHERE id = %s",
        tuple(values)
    )

    return (
        cur.rowcount > 0
    )


def mark_verification_sent(
    cur,
    case: Dict[str, Any]
):
    cur.execute("""
        UPDATE cases
        SET
            client_verification_status = 'SENT',
            client_verification_sent_at =
                CURRENT_TIMESTAMP
        WHERE id = %s
    """, (
        case["id"],
    ))


def mark_case_verified(
    cur,
    case: Dict[str, Any]
):
    cur.execute("""
        UPDATE cases
        SET
            client_verification_status =
                'CONFIRMED',
            client_verified_at =
                CURRENT_TIMESTAMP,
            client_correction_note = NULL
        WHERE id = %s
    """, (
        case["id"],
    ))

    client_columns = table_columns(
        cur,
        "clients"
    )

    if not client_columns:
        return

    assignments = []

    if "verification_status" in client_columns:
        assignments.append(
            "verification_status = 'CONFIRMED'"
        )

    if "verified_at" in client_columns:
        assignments.append(
            "verified_at = CURRENT_TIMESTAMP"
        )

    if not assignments:
        return

    where_parts = []
    values = []

    if case.get("client_id"):
        where_parts.append(
            "id = %s"
        )
        values.append(
            case["client_id"]
        )

    if (
        case.get("ad_client_id")
        and "ad_client_id" in client_columns
    ):
        where_parts.append(
            "ad_client_id = %s"
        )
        values.append(
            case["ad_client_id"]
        )

    if where_parts:
        cur.execute(
            "UPDATE clients SET "
            + ", ".join(assignments)
            + " WHERE "
            + " OR ".join(where_parts),
            tuple(values)
        )


def mark_change_requested(
    cur,
    case: Dict[str, Any],
    note: str
):
    cur.execute("""
        UPDATE cases
        SET
            client_verification_status =
                'CHANGE_REQUESTED',
            client_correction_note = %s
        WHERE id = %s
    """, (
        note,
        case["id"]
    ))


def get_message_history(
    cur,
    case_value: str,
    limit: int = 20
) -> List[Dict[str, Any]]:
    columns = table_columns(
        cur,
        "client_messages"
    )

    if not columns:
        return []

    wanted = [
        "id",
        "case_id",
        "message_type",
        "delivery_status",
        "created_at",
        "sent_at",
        "communication_ref",
        "phone_number",
        "message_text",
    ]

    select_fields = [
        field
        for field in wanted
        if field in columns
    ]

    if not select_fields:
        return []

    case_column = (
        "related_case_id"
        if "related_case_id" in columns
        else "case_id"
    )

    cur.execute(
        "SELECT "
        + ", ".join(select_fields)
        + " FROM client_messages "
        f"WHERE LOWER(TRIM(COALESCE({case_column}, ''))) "
        "= LOWER(TRIM(%s)) "
        "ORDER BY id DESC LIMIT %s",
        (
            case_value,
            limit
        )
    )

    rows = cur.fetchall()

    return [
        dict(
            zip(
                select_fields,
                row
            )
        )
        for row in rows
    ]
