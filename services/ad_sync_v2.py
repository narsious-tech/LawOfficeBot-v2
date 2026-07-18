import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
import requests

from config import DATABASE_URL
from utils.drive import get_or_create_case_folder


AD_API = os.getenv("AD_API", "").rstrip("/")
AD_EMAIL = os.getenv("AD_EMAIL")
AD_PASSWORD = os.getenv("AD_PASSWORD")


MOBILE_KEYS = (
    "mobile",
    "mobile_no",
    "mobile_number",
    "client_mobile",
    "client_mobile_no",
    "client_mobile_number",
    "phone",
    "phone_no",
    "phone_number",
    "contact",
    "contact_no",
    "contact_number",
    "whatsapp",
    "whatsapp_no",
    "whatsapp_number",
)

EMAIL_KEYS = (
    "email",
    "email_id",
    "client_email",
)

ADDRESS_KEYS = (
    "address",
    "client_address",
    "residential_address",
    "office_address",
)

CLIENT_ID_KEYS = (
    "client_id",
    "ad_client_id",
    "client_uuid",
)

CLIENT_NAME_KEYS = (
    "client_name",
    "name",
    "full_name",
)

CASE_NUMBER_KEYS = (
    "case_number",
    "case_no",
    "registration_number",
    "registration_no",
)

NEXT_HEARING_KEYS = (
    "next_date",
    "next_hearing",
    "next_hearing_date",
    "hearing_date",
)

CASE_TITLE_KEYS = (
    "case_title",
    "title",
)

CASE_TYPE_KEYS = (
    "case_type",
    "case_type_name",
)

COURT_KEYS = (
    "court_name",
    "court",
)

JUDGE_KEYS = (
    "judge_name",
    "judge",
)

OPPOSITE_KEYS = (
    "opposite_party",
    "verses_name",
    "respondent_name",
)

STATUS_KEYS = (
    "status",
    "case_status",
)

CREATED_AT_KEYS = (
    "created_at",
    "created",
)


def normalize_mobile(value: Any) -> str:
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
        return digits

    return ""


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (dict, list, tuple)):
        return ""

    return str(value).strip()


def first_nonblank(
    mapping: Dict[str, Any],
    keys: Iterable[str]
) -> Any:
    for key in keys:
        value = mapping.get(key)

        if clean_text(value):
            return value

    return None


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from iter_dicts(child)

    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def deep_first(
    payload: Dict[str, Any],
    keys: Iterable[str]
) -> Any:
    for mapping in iter_dicts(payload):
        value = first_nonblank(
            mapping,
            keys
        )

        if value is not None:
            return value

    return None


def client_candidate_dicts(
    payload: Dict[str, Any]
) -> List[Dict[str, Any]]:
    candidates = []

    preferred_keys = (
        "client",
        "client_details",
        "client_data",
        "party",
        "litigant",
    )

    for key in preferred_keys:
        value = payload.get(key)

        if isinstance(value, dict):
            candidates.append(value)

    candidates.append(payload)

    for mapping in iter_dicts(payload):
        if mapping not in candidates:
            candidates.append(mapping)

    return candidates


def extract_client_data(
    case_payload: Dict[str, Any]
) -> Dict[str, Any]:
    candidates = client_candidate_dicts(
        case_payload
    )

    ad_client_id = None
    client_name = ""
    mobile = ""
    email = ""
    address = ""

    for mapping in candidates:
        if not ad_client_id:
            raw_id = first_nonblank(
                mapping,
                CLIENT_ID_KEYS + ("id",)
            )

            if raw_id is not None:
                ad_client_id = clean_text(
                    raw_id
                )

        if not client_name:
            client_name = clean_text(
                first_nonblank(
                    mapping,
                    CLIENT_NAME_KEYS
                )
            )

        if not mobile:
            raw_mobile = first_nonblank(
                mapping,
                MOBILE_KEYS
            )

            mobile = normalize_mobile(
                raw_mobile
            )

        if not email:
            email = clean_text(
                first_nonblank(
                    mapping,
                    EMAIL_KEYS
                )
            )

        if not address:
            address = clean_text(
                first_nonblank(
                    mapping,
                    ADDRESS_KEYS
                )
            )

        if (
            ad_client_id
            and client_name
            and mobile
            and email
            and address
        ):
            break

    return {
        "ad_client_id": (
            ad_client_id
            or None
        ),
        "client_name": (
            client_name
            or "Unknown Client"
        ),
        "mobile": mobile,
        "email": email,
        "address": address,
    }


def extract_case_data(
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    client_data = extract_client_data(
        payload
    )

    case_number = clean_text(
        deep_first(
            payload,
            CASE_NUMBER_KEYS
        )
    )

    ad_case_id = clean_text(
        payload.get("id")
        or payload.get("case_id")
        or payload.get("uuid")
    )

    return {
        "ad_case_id": (
            ad_case_id
            or None
        ),
        "case_number": case_number,
        "case_title": clean_text(
            deep_first(
                payload,
                CASE_TITLE_KEYS
            )
        ),
        "case_type": clean_text(
            deep_first(
                payload,
                CASE_TYPE_KEYS
            )
        ),
        "court_name": clean_text(
            deep_first(
                payload,
                COURT_KEYS
            )
        ),
        "judge_name": clean_text(
            deep_first(
                payload,
                JUDGE_KEYS
            )
        ),
        "opposite_party": clean_text(
            deep_first(
                payload,
                OPPOSITE_KEYS
            )
        ),
        "next_hearing": clean_text(
            deep_first(
                payload,
                NEXT_HEARING_KEYS
            )
        ),
        "status": clean_text(
            deep_first(
                payload,
                STATUS_KEYS
            )
        ) or "pending",
        "ad_created_at": clean_text(
            deep_first(
                payload,
                CREATED_AT_KEYS
            )
        ),
        **client_data,
    }


def ensure_sync_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            ad_client_id TEXT UNIQUE,
            client_name TEXT NOT NULL,
            mobile TEXT,
            whatsapp_number TEXT,
            email TEXT,
            address TEXT,
            verification_status TEXT
                DEFAULT 'NOT_SENT',
            verification_sent_at TIMESTAMP,
            verified_at TIMESTAMP,
            correction_note TEXT,
            ad_sync_status TEXT
                DEFAULT 'PENDING',
            ad_synced_at TIMESTAMP,
            ad_sync_message TEXT,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            client_id INTEGER
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            ad_client_id TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            mobile TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            ad_case_id TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            case_number TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            case_title TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            judge_name TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            next_hearing TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            ad_sync_status TEXT
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            ad_created_at TIMESTAMP
    """)

    cur.execute("""
        ALTER TABLE cases
        ADD COLUMN IF NOT EXISTS
            ad_sync_message TEXT
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            clients_name_idx
        ON clients (
            LOWER(TRIM(client_name))
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            clients_mobile_idx
        ON clients (mobile)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            cases_ad_client_idx
        ON cases (ad_client_id)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            cases_case_number_idx
        ON cases (
            LOWER(TRIM(case_number))
        )
    """)


def login() -> Tuple[str, Optional[str]]:
    if not AD_API:
        raise RuntimeError(
            "AD_API is missing."
        )

    response = requests.post(
        f"{AD_API}/login",
        json={
            "email": AD_EMAIL,
            "password": AD_PASSWORD,
        },
        timeout=(10, 45)
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            data.get("message")
            or "Advocate Diaries login failed."
        )

    token_data = data.get("data") or {}

    access_token = token_data.get(
        "access_token"
    )

    if not access_token:
        raise RuntimeError(
            "Advocate Diaries access token is missing."
        )

    return (
        access_token,
        token_data.get("refresh_token")
    )


def fetch_all_cases(
    access_token: str
) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": (
            f"Bearer {access_token}"
        ),
        "Accept": "application/json",
    }

    all_cases = []
    page = 1

    while True:
        response = requests.get(
            f"{AD_API}/court_cases",
            params={"page": page},
            headers=headers,
            timeout=(10, 60)
        )

        if response.status_code == 404:
            break

        response.raise_for_status()

        body = response.json()
        data = body.get("data") or []

        if not data:
            break

        all_cases.extend(data)
        page += 1

    return all_cases


def find_existing_client_id(
    cur,
    client: Dict[str, Any]
) -> Optional[int]:
    ad_client_id = client.get(
        "ad_client_id"
    )

    mobile = client.get("mobile")
    name = client.get("client_name")

    if ad_client_id:
        cur.execute("""
            SELECT id
            FROM clients
            WHERE ad_client_id = %s
            LIMIT 1
        """, (
            ad_client_id,
        ))

        row = cur.fetchone()

        if row:
            return int(row[0])

    if mobile:
        cur.execute("""
            SELECT id
            FROM clients
            WHERE
                REGEXP_REPLACE(
                    COALESCE(mobile, ''),
                    '[^0-9]',
                    '',
                    'g'
                ) = %s
                OR
                REGEXP_REPLACE(
                    COALESCE(
                        whatsapp_number,
                        ''
                    ),
                    '[^0-9]',
                    '',
                    'g'
                ) = %s
            ORDER BY id ASC
            LIMIT 1
        """, (
            mobile,
            mobile
        ))

        row = cur.fetchone()

        if row:
            return int(row[0])

    if name and name != "Unknown Client":
        cur.execute("""
            SELECT id
            FROM clients
            WHERE
                LOWER(TRIM(client_name))
                =
                LOWER(TRIM(%s))
            ORDER BY id ASC
            LIMIT 2
        """, (
            name,
        ))

        rows = cur.fetchall()

        if len(rows) == 1:
            return int(rows[0][0])

    return None


def upsert_client(
    cur,
    client: Dict[str, Any]
) -> Tuple[int, Dict[str, int]]:
    stats = {
        "client_created": 0,
        "client_updated": 0,
        "mobile_added": 0,
        "email_added": 0,
        "address_added": 0,
    }

    existing_id = find_existing_client_id(
        cur,
        client
    )

    if existing_id:
        cur.execute("""
            SELECT
                mobile,
                whatsapp_number,
                email,
                address
            FROM clients
            WHERE id = %s
        """, (
            existing_id,
        ))

        old = cur.fetchone() or (
            None,
            None,
            None,
            None,
        )

        if (
            not clean_text(old[0])
            and client.get("mobile")
        ):
            stats["mobile_added"] += 1

        if (
            not clean_text(old[2])
            and client.get("email")
        ):
            stats["email_added"] += 1

        if (
            not clean_text(old[3])
            and client.get("address")
        ):
            stats["address_added"] += 1

        cur.execute("""
            UPDATE clients
            SET
                ad_client_id = COALESCE(
                    NULLIF(%s, ''),
                    ad_client_id
                ),
                client_name = COALESCE(
                    NULLIF(%s, ''),
                    client_name
                ),
                mobile = CASE
                    WHEN NULLIF(%s, '') IS NOT NULL
                    THEN %s
                    ELSE mobile
                END,
                whatsapp_number = CASE
                    WHEN NULLIF(%s, '') IS NOT NULL
                    THEN %s
                    ELSE whatsapp_number
                END,
                email = CASE
                    WHEN NULLIF(%s, '') IS NOT NULL
                    THEN %s
                    ELSE email
                END,
                address = CASE
                    WHEN NULLIF(%s, '') IS NOT NULL
                    THEN %s
                    ELSE address
                END,
                ad_sync_status = 'MIRRORED',
                ad_synced_at =
                    CURRENT_TIMESTAMP,
                ad_sync_message =
                    'Updated by Advocate Diaries Sync v2',
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            client.get("ad_client_id"),
            client.get("client_name"),
            client.get("mobile"),
            client.get("mobile"),
            client.get("mobile"),
            client.get("mobile"),
            client.get("email"),
            client.get("email"),
            client.get("address"),
            client.get("address"),
            existing_id,
        ))

        stats["client_updated"] += 1

        return existing_id, stats

    cur.execute("""
        INSERT INTO clients
        (
            ad_client_id,
            client_name,
            mobile,
            whatsapp_number,
            email,
            address,
            ad_sync_status,
            ad_synced_at,
            ad_sync_message
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, 'MIRRORED',
            CURRENT_TIMESTAMP,
            'Created by Advocate Diaries Sync v2'
        )
        RETURNING id
    """, (
        client.get("ad_client_id"),
        client.get("client_name"),
        client.get("mobile") or None,
        client.get("mobile") or None,
        client.get("email") or None,
        client.get("address") or None,
    ))

    stats["client_created"] += 1

    if client.get("mobile"):
        stats["mobile_added"] += 1

    if client.get("email"):
        stats["email_added"] += 1

    if client.get("address"):
        stats["address_added"] += 1

    return int(cur.fetchone()[0]), stats


def find_existing_case_id(
    cur,
    case: Dict[str, Any]
) -> Optional[int]:
    if case.get("ad_case_id"):
        cur.execute("""
            SELECT id
            FROM cases
            WHERE ad_case_id = %s
            LIMIT 1
        """, (
            case["ad_case_id"],
        ))

        row = cur.fetchone()

        if row:
            return int(row[0])

    case_number = case.get(
        "case_number"
    )

    if case_number:
        cur.execute("""
            SELECT id
            FROM cases
            WHERE
                LOWER(TRIM(
                    COALESCE(case_number, '')
                ))
                =
                LOWER(TRIM(%s))
                OR
                LOWER(TRIM(
                    COALESCE(case_id, '')
                ))
                =
                LOWER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
        """, (
            case_number,
            case_number
        ))

        row = cur.fetchone()

        if row:
            return int(row[0])

    return None


def parse_timestamp(
    value: str
) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()

    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None


def sync_case(
    cur,
    case: Dict[str, Any],
    *,
    client_local_id: int
) -> Tuple[str, bool, bool]:
    case_number = case.get(
        "case_number"
    )

    if not case_number:
        return "skipped", False, False

    existing_id = find_existing_case_id(
        cur,
        case
    )

    cur.execute("""
        SELECT
            drive_folder_id,
            drive_folder_link
        FROM cases
        WHERE id = %s
    """, (
        existing_id or -1,
    ))

    folder_row = cur.fetchone()

    folder_id = (
        folder_row[0]
        if folder_row
        else None
    )

    folder_link = (
        folder_row[1]
        if folder_row
        else None
    )

    folder_created = False
    folder_reused = False

    if not folder_id:
        folder_id, folder_link = (
            get_or_create_case_folder(
                case_number
            )
        )

        folder_created = True

    else:
        folder_reused = True

    values = (
        case.get("ad_case_id"),
        case.get("ad_client_id"),
        client_local_id,
        case_number,
        case_number,
        case.get("case_title"),
        case.get("client_name"),
        case.get("mobile"),
        case.get("case_type"),
        case.get("court_name"),
        case.get("judge_name"),
        case.get("opposite_party"),
        case.get("next_hearing"),
        case.get("next_hearing"),
        case.get("status"),
        folder_id,
        folder_link,
        "MIRRORED",
        parse_timestamp(
            case.get("ad_created_at")
        ),
        "Mirrored by Advocate Diaries Sync v2",
    )

    if existing_id:
        cur.execute("""
            UPDATE cases
            SET
                ad_case_id = COALESCE(
                    NULLIF(%s, ''),
                    ad_case_id
                ),
                ad_client_id = COALESCE(
                    NULLIF(%s, ''),
                    ad_client_id
                ),
                client_id = %s,
                case_id = COALESCE(
                    NULLIF(%s, ''),
                    case_id
                ),
                case_number = COALESCE(
                    NULLIF(%s, ''),
                    case_number
                ),
                case_title = COALESCE(
                    NULLIF(%s, ''),
                    case_title
                ),
                client_name = COALESCE(
                    NULLIF(%s, ''),
                    client_name
                ),
                mobile = CASE
                    WHEN NULLIF(%s, '') IS NOT NULL
                    THEN %s
                    ELSE mobile
                END,
                case_type = COALESCE(
                    NULLIF(%s, ''),
                    case_type
                ),
                court_name = COALESCE(
                    NULLIF(%s, ''),
                    court_name
                ),
                judge_name = COALESCE(
                    NULLIF(%s, ''),
                    judge_name
                ),
                opposite_party = COALESCE(
                    NULLIF(%s, ''),
                    opposite_party
                ),
                hearing_date = COALESCE(
                    NULLIF(%s, ''),
                    hearing_date
                ),
                next_hearing = COALESCE(
                    NULLIF(%s, ''),
                    next_hearing
                ),
                status = COALESCE(
                    NULLIF(%s, ''),
                    status
                ),
                drive_folder_id = COALESCE(
                    drive_folder_id,
                    %s
                ),
                drive_folder_link = COALESCE(
                    drive_folder_link,
                    %s
                ),
                ad_sync_status = %s,
                ad_created_at = COALESCE(
                    ad_created_at,
                    %s
                ),
                ad_sync_message = %s
            WHERE id = %s
        """, (
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            values[7],
            values[7],
            values[8],
            values[9],
            values[10],
            values[11],
            values[12],
            values[13],
            values[14],
            values[15],
            values[16],
            values[17],
            values[18],
            values[19],
            existing_id,
        ))

        return (
            "updated",
            folder_created,
            folder_reused
        )

    cur.execute("""
        INSERT INTO cases
        (
            ad_case_id,
            ad_client_id,
            client_id,
            case_id,
            case_number,
            case_title,
            client_name,
            mobile,
            case_type,
            court_name,
            judge_name,
            opposite_party,
            hearing_date,
            next_hearing,
            status,
            drive_folder_id,
            drive_folder_link,
            ad_sync_status,
            ad_created_at,
            ad_sync_message
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """, values)

    return (
        "added",
        folder_created,
        folder_reused
    )


def repair_existing_mobile_links(
    cur
) -> int:
    cur.execute("""
        UPDATE cases c
        SET
            mobile = COALESCE(
                NULLIF(TRIM(c.mobile), ''),
                NULLIF(TRIM(cl.mobile), ''),
                NULLIF(
                    TRIM(
                        cl.whatsapp_number
                    ),
                    ''
                )
            ),
            client_id = COALESCE(
                c.client_id,
                cl.id
            ),
            ad_client_id = COALESCE(
                c.ad_client_id,
                cl.ad_client_id
            )
        FROM clients cl
        WHERE
            (
                c.client_id = cl.id
                OR (
                    c.ad_client_id IS NOT NULL
                    AND
                    c.ad_client_id =
                        cl.ad_client_id
                )
                OR (
                    LOWER(TRIM(
                        c.client_name
                    ))
                    =
                    LOWER(TRIM(
                        cl.client_name
                    ))
                )
            )
            AND (
                TRIM(
                    COALESCE(
                        c.mobile,
                        ''
                    )
                ) = ''
                OR c.client_id IS NULL
            )
    """)

    return int(cur.rowcount)


def run_sync_v2() -> Dict[str, int]:
    access_token, _ = login()
    payloads = fetch_all_cases(
        access_token
    )

    conn = psycopg2.connect(
        DATABASE_URL
    )

    cur = conn.cursor()

    stats = {
        "total": len(payloads),
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "clients_created": 0,
        "clients_updated": 0,
        "mobiles_added": 0,
        "emails_added": 0,
        "addresses_added": 0,
        "folders_created": 0,
        "folders_reused": 0,
        "cases_repaired": 0,
        "payloads_with_mobile": 0,
    }

    try:
        ensure_sync_schema(cur)

        for payload in payloads:
            case = extract_case_data(
                payload
            )

            if case.get("mobile"):
                stats[
                    "payloads_with_mobile"
                ] += 1

            client_local_id, client_stats = (
                upsert_client(
                    cur,
                    case
                )
            )

            for key, value in client_stats.items():
                stats[key + "s" if False else key] = (
                    stats.get(key, 0)
                    + value
                )

            action, created, reused = (
                sync_case(
                    cur,
                    case,
                    client_local_id=(
                        client_local_id
                    )
                )
            )

            if action == "added":
                stats["added"] += 1
            elif action == "updated":
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

            if created:
                stats["folders_created"] += 1

            if reused:
                stats["folders_reused"] += 1

        stats["cases_repaired"] = (
            repair_existing_mobile_links(
                cur
            )
        )

        cur.execute("""
            INSERT INTO sync_logs
            (
                sync_type,
                total_fetched,
                added_count,
                updated_count,
                folders_created,
                folders_reused,
                skipped_count,
                status,
                message
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
        """, (
            "advocate_diaries_cases_v2",
            stats["total"],
            stats["added"],
            stats["updated"],
            stats["folders_created"],
            stats["folders_reused"],
            stats["skipped"],
            "SUCCESS",
            (
                "Advocate Diaries Sync v2 completed; "
                f"payloads_with_mobile="
                f"{stats['payloads_with_mobile']}; "
                f"mobiles_added="
                f"{stats['mobiles_added']}; "
                f"cases_repaired="
                f"{stats['cases_repaired']}"
            ),
        ))

        conn.commit()

        return stats

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()
