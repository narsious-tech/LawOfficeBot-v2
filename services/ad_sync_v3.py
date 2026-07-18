import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
import requests

from config import DATABASE_URL
from utils.drive import get_or_create_case_folder


AD_API = os.getenv(
    "AD_API",
    "https://advocatediaries.com/api/v1"
).rstrip("/")

AD_EMAIL = os.getenv("AD_EMAIL")
AD_PASSWORD = os.getenv("AD_PASSWORD")


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(
        value,
        (dict, list, tuple)
    ):
        return ""

    return str(value).strip()


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

    if (
        len(digits) == 12
        and digits.startswith("91")
    ):
        return digits

    return ""


def first_nonblank(
    mapping: Dict[str, Any],
    keys: Iterable[str]
) -> Any:
    for key in keys:
        value = mapping.get(key)

        if clean_text(value):
            return value

    return None


def login() -> str:
    if not AD_EMAIL or not AD_PASSWORD:
        raise RuntimeError(
            "AD_EMAIL or AD_PASSWORD is missing."
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

    body = response.json()

    if not body.get("success"):
        raise RuntimeError(
            body.get("message")
            or "Advocate Diaries login failed."
        )

    token = (
        body.get("data", {})
        .get("access_token")
    )

    if not token:
        raise RuntimeError(
            "Advocate Diaries access token is missing."
        )

    return token


def auth_headers(
    access_token: str
) -> Dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {access_token}"
        ),
        "Accept": "application/json",
    }


def extract_list_from_response(
    body: Dict[str, Any]
) -> List[Dict[str, Any]]:
    data = body.get("data") or []

    if isinstance(data, list):
        return [
            item
            for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        for key in (
            "items",
            "cases",
            "clients",
            "records",
            "data",
        ):
            value = data.get(key)

            if isinstance(value, list):
                return [
                    item
                    for item in value
                    if isinstance(item, dict)
                ]

    return []


def fetch_all_cases(
    access_token: str
) -> List[Dict[str, Any]]:
    headers = auth_headers(
        access_token
    )

    all_cases = []
    page = 1

    while True:
        response = requests.get(
            f"{AD_API}/court_cases",
            params={"page": page},
            headers=headers,
            timeout=(10, 60)
        )

        response.raise_for_status()

        body = response.json()
        rows = extract_list_from_response(
            body
        )

        if not rows:
            break

        all_cases.extend(rows)

        pagination = body.get(
            "pagination"
        ) or body.get(
            "meta"
        ) or {}

        total_pages = (
            pagination.get("total_pages")
            or pagination.get("last_page")
        )

        if total_pages:
            if page >= int(total_pages):
                break

        elif len(rows) < 1:
            break

        page += 1

    return all_cases


def fetch_client_detail(
    access_token: str,
    client_id: str,
    *,
    retries: int = 3
) -> Dict[str, Any]:
    headers = auth_headers(
        access_token
    )

    last_error = None

    for attempt in range(
        1,
        retries + 1
    ):
        try:
            response = requests.get(
                f"{AD_API}/clients/{client_id}",
                headers=headers,
                timeout=(10, 45)
            )

            response.raise_for_status()

            body = response.json()

            if not body.get("success"):
                raise RuntimeError(
                    body.get("message")
                    or "Client details not found."
                )

            data = body.get("data") or {}

            if not isinstance(data, dict):
                raise RuntimeError(
                    "Unexpected client response format."
                )

            return data

        except Exception as exc:
            last_error = exc

            if attempt < retries:
                time.sleep(
                    0.75 * attempt
                )

    raise RuntimeError(
        f"Client {client_id} fetch failed: "
        f"{type(last_error).__name__}: "
        f"{last_error}"
    )


def parse_case_payload(
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    client_id = clean_text(
        payload.get("client_id")
    )

    case_number = clean_text(
        first_nonblank(
            payload,
            (
                "case_number",
                "case_no",
                "registration_number",
            )
        )
    )

    return {
        "ad_case_id": clean_text(
            payload.get("id")
            or payload.get("case_id")
        ) or None,

        "ad_client_id": (
            client_id
            or None
        ),

        "client_name": clean_text(
            payload.get("client_name")
        ),

        "case_number": case_number,

        "case_title": clean_text(
            payload.get("case_title")
        ),

        "case_type": clean_text(
            payload.get("case_type_name")
            or payload.get("case_type")
        ),

        "court_name": clean_text(
            payload.get("court_name")
            or payload.get("court")
        ),

        "judge_name": clean_text(
            payload.get("judge_name")
            or payload.get("judge")
        ),

        "opposite_party": clean_text(
            payload.get("verses_name")
            or payload.get(
                "opposite_party"
            )
        ),

        "next_hearing": clean_text(
            payload.get("next_date")
            or payload.get(
                "next_hearing"
            )
        ),

        "status": clean_text(
            payload.get("status")
        ) or "pending",

        "purpose": clean_text(
            payload.get("purpose")
        ),

        "ad_created_at": clean_text(
            payload.get("created_at")
        ),

        "ad_updated_at": clean_text(
            payload.get("updated_at")
        ),
    }


def parse_client_payload(
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    primary_phone = normalize_mobile(
        payload.get("primary_phone")
    )

    other_phone = normalize_mobile(
        payload.get("other_phone")
    )

    primary_email = clean_text(
        payload.get("primary_email")
    )

    other_email = clean_text(
        payload.get("other_email")
    )

    address_parts = [
        clean_text(
            payload.get("address")
        ),
        clean_text(
            payload.get("city")
        ),
        clean_text(
            payload.get("state")
        ),
        clean_text(
            payload.get("country")
        ),
    ]

    full_address = ", ".join(
        part
        for part in address_parts
        if part
    )

    return {
        "ad_client_id": clean_text(
            payload.get("id")
        ) or None,

        "client_name": clean_text(
            payload.get("name")
        ) or "Unknown Client",

        "mobile": (
            primary_phone
            or other_phone
        ),

        "other_phone": other_phone,

        "email": (
            primary_email
            or other_email
        ),

        "other_email": other_email,

        "address": full_address,

        "city": clean_text(
            payload.get("city")
        ),

        "state": clean_text(
            payload.get("state")
        ),

        "country": clean_text(
            payload.get("country")
        ),

        "gst_number": clean_text(
            payload.get("gst_number")
        ),

        "ad_created_at": clean_text(
            payload.get("created_at")
        ),

        "ad_updated_at": clean_text(
            payload.get("updated_at")
        ),
    }


def parse_timestamp(
    value: str
) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()

    for candidate in (
        text,
        text.replace(
            "Z",
            "+00:00"
        ),
    ):
        try:
            return datetime.fromisoformat(
                candidate
            )
        except ValueError:
            continue

    return None


def ensure_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            ad_client_id TEXT UNIQUE,
            client_name TEXT NOT NULL,
            mobile TEXT,
            whatsapp_number TEXT,
            other_phone TEXT,
            email TEXT,
            other_email TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            gst_number TEXT,
            verification_status TEXT
                DEFAULT 'NOT_SENT',
            verification_sent_at TIMESTAMP,
            verified_at TIMESTAMP,
            correction_note TEXT,
            ad_sync_status TEXT
                DEFAULT 'PENDING',
            ad_synced_at TIMESTAMP,
            ad_created_at TIMESTAMP,
            ad_updated_at TIMESTAMP,
            ad_sync_message TEXT,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
    """)

    client_columns = {
        "ad_client_id": "TEXT",
        "mobile": "TEXT",
        "whatsapp_number": "TEXT",
        "other_phone": "TEXT",
        "email": "TEXT",
        "other_email": "TEXT",
        "address": "TEXT",
        "city": "TEXT",
        "state": "TEXT",
        "country": "TEXT",
        "gst_number": "TEXT",
        "ad_sync_status": "TEXT",
        "ad_synced_at": "TIMESTAMP",
        "ad_created_at": "TIMESTAMP",
        "ad_updated_at": "TIMESTAMP",
        "ad_sync_message": "TEXT",
        "updated_at": "TIMESTAMP",
    }

    for column, column_type in (
        client_columns.items()
    ):
        cur.execute(
            f"""
            ALTER TABLE clients
            ADD COLUMN IF NOT EXISTS
                {column} {column_type}
            """
        )

    case_columns = {
        "client_id": "INTEGER",
        "ad_client_id": "TEXT",
        "mobile": "TEXT",
        "ad_case_id": "TEXT",
        "case_number": "TEXT",
        "case_title": "TEXT",
        "judge_name": "TEXT",
        "next_hearing": "TEXT",
        "notes": "TEXT",
        "ad_sync_status": "TEXT",
        "ad_created_at": "TIMESTAMP",
        "ad_sync_message": "TEXT",
    }

    for column, column_type in (
        case_columns.items()
    ):
        cur.execute(
            f"""
            ALTER TABLE cases
            ADD COLUMN IF NOT EXISTS
                {column} {column_type}
            """
        )

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            clients_ad_client_unique_idx
        ON clients(ad_client_id)
        WHERE ad_client_id IS NOT NULL
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            clients_mobile_idx
        ON clients(mobile)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            cases_ad_client_idx
        ON cases(ad_client_id)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
            cases_case_number_idx
        ON cases(
            LOWER(TRIM(case_number))
        )
    """)


def find_client_id(
    cur,
    client: Dict[str, Any]
) -> Optional[int]:
    ad_client_id = client.get(
        "ad_client_id"
    )

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

    mobile = client.get("mobile")

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

    name = client.get(
        "client_name"
    )

    if (
        name
        and name != "Unknown Client"
    ):
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
        "clients_created": 0,
        "clients_updated": 0,
        "mobiles_imported": 0,
        "emails_imported": 0,
        "addresses_imported": 0,
    }

    existing_id = find_client_id(
        cur,
        client
    )

    if existing_id:
        cur.execute("""
            SELECT
                mobile,
                email,
                address
            FROM clients
            WHERE id = %s
        """, (
            existing_id,
        ))

        old_mobile, old_email, old_address = (
            cur.fetchone()
            or (None, None, None)
        )

        if (
            not clean_text(old_mobile)
            and client.get("mobile")
        ):
            stats[
                "mobiles_imported"
            ] += 1

        if (
            not clean_text(old_email)
            and client.get("email")
        ):
            stats[
                "emails_imported"
            ] += 1

        if (
            not clean_text(old_address)
            and client.get("address")
        ):
            stats[
                "addresses_imported"
            ] += 1

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

                mobile = COALESCE(
                    NULLIF(%s, ''),
                    mobile
                ),

                whatsapp_number = COALESCE(
                    NULLIF(%s, ''),
                    whatsapp_number,
                    mobile
                ),

                other_phone = COALESCE(
                    NULLIF(%s, ''),
                    other_phone
                ),

                email = COALESCE(
                    NULLIF(%s, ''),
                    email
                ),

                other_email = COALESCE(
                    NULLIF(%s, ''),
                    other_email
                ),

                address = COALESCE(
                    NULLIF(%s, ''),
                    address
                ),

                city = COALESCE(
                    NULLIF(%s, ''),
                    city
                ),

                state = COALESCE(
                    NULLIF(%s, ''),
                    state
                ),

                country = COALESCE(
                    NULLIF(%s, ''),
                    country
                ),

                gst_number = COALESCE(
                    NULLIF(%s, ''),
                    gst_number
                ),

                ad_sync_status = 'MIRRORED',
                ad_synced_at =
                    CURRENT_TIMESTAMP,

                ad_created_at = COALESCE(
                    ad_created_at,
                    %s
                ),

                ad_updated_at = COALESCE(
                    %s,
                    ad_updated_at
                ),

                ad_sync_message =
                    'Updated by Advocate Diaries Sync v3',

                updated_at =
                    CURRENT_TIMESTAMP

            WHERE id = %s
        """, (
            client.get("ad_client_id"),
            client.get("client_name"),
            client.get("mobile"),
            client.get("mobile"),
            client.get("other_phone"),
            client.get("email"),
            client.get("other_email"),
            client.get("address"),
            client.get("city"),
            client.get("state"),
            client.get("country"),
            client.get("gst_number"),
            parse_timestamp(
                client.get(
                    "ad_created_at"
                )
            ),
            parse_timestamp(
                client.get(
                    "ad_updated_at"
                )
            ),
            existing_id,
        ))

        stats[
            "clients_updated"
        ] += 1

        return existing_id, stats

    cur.execute("""
        INSERT INTO clients (
            ad_client_id,
            client_name,
            mobile,
            whatsapp_number,
            other_phone,
            email,
            other_email,
            address,
            city,
            state,
            country,
            gst_number,
            ad_sync_status,
            ad_synced_at,
            ad_created_at,
            ad_updated_at,
            ad_sync_message
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s,
            'MIRRORED',
            CURRENT_TIMESTAMP,
            %s, %s,
            'Created by Advocate Diaries Sync v3'
        )
        RETURNING id
    """, (
        client.get("ad_client_id"),
        client.get("client_name"),
        client.get("mobile") or None,
        client.get("mobile") or None,
        client.get("other_phone") or None,
        client.get("email") or None,
        client.get("other_email") or None,
        client.get("address") or None,
        client.get("city") or None,
        client.get("state") or None,
        client.get("country") or None,
        client.get("gst_number") or None,
        parse_timestamp(
            client.get("ad_created_at")
        ),
        parse_timestamp(
            client.get("ad_updated_at")
        ),
    ))

    client_id = int(
        cur.fetchone()[0]
    )

    stats[
        "clients_created"
    ] += 1

    if client.get("mobile"):
        stats[
            "mobiles_imported"
        ] += 1

    if client.get("email"):
        stats[
            "emails_imported"
        ] += 1

    if client.get("address"):
        stats[
            "addresses_imported"
        ] += 1

    return client_id, stats


def find_case_id(
    cur,
    case: Dict[str, Any]
) -> Optional[int]:
    ad_case_id = case.get(
        "ad_case_id"
    )

    if ad_case_id:
        cur.execute("""
            SELECT id
            FROM cases
            WHERE ad_case_id = %s
            LIMIT 1
        """, (
            ad_case_id,
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
                LOWER(
                    TRIM(
                        COALESCE(
                            case_number,
                            ''
                        )
                    )
                )
                =
                LOWER(TRIM(%s))
                OR
                LOWER(
                    TRIM(
                        COALESCE(
                            case_id,
                            ''
                        )
                    )
                )
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


def upsert_case(
    cur,
    case: Dict[str, Any],
    client: Dict[str, Any],
    client_local_id: int
) -> Tuple[str, bool, bool]:
    case_number = case.get(
        "case_number"
    )

    if not case_number:
        return (
            "skipped",
            False,
            False
        )

    existing_id = find_case_id(
        cur,
        case
    )

    folder_id = None
    folder_link = None

    if existing_id:
        cur.execute("""
            SELECT
                drive_folder_id,
                drive_folder_link
            FROM cases
            WHERE id = %s
        """, (
            existing_id,
        ))

        row = cur.fetchone()

        if row:
            folder_id = row[0]
            folder_link = row[1]

    folder_created = False
    folder_reused = False

    if folder_id:
        folder_reused = True
    else:
        folder_id, folder_link = (
            get_or_create_case_folder(
                case_number
            )
        )
        folder_created = True

    mobile = client.get(
        "mobile"
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

                mobile = COALESCE(
                    NULLIF(%s, ''),
                    mobile
                ),

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

                notes = COALESCE(
                    notes,
                    NULLIF(%s, '')
                ),

                drive_folder_id = COALESCE(
                    drive_folder_id,
                    %s
                ),

                drive_folder_link = COALESCE(
                    drive_folder_link,
                    %s
                ),

                ad_sync_status = 'MIRRORED',

                ad_created_at = COALESCE(
                    ad_created_at,
                    %s
                ),

                ad_sync_message =
                    'Updated by Advocate Diaries Sync v3'

            WHERE id = %s
        """, (
            case.get("ad_case_id"),
            case.get("ad_client_id"),
            client_local_id,
            case_number,
            case_number,
            case.get("case_title"),
            client.get("client_name")
            or case.get("client_name"),
            mobile,
            case.get("case_type"),
            case.get("court_name"),
            case.get("judge_name"),
            case.get("opposite_party"),
            case.get("next_hearing"),
            case.get("next_hearing"),
            case.get("status"),
            case.get("purpose"),
            folder_id,
            folder_link,
            parse_timestamp(
                case.get(
                    "ad_created_at"
                )
            ),
            existing_id,
        ))

        return (
            "updated",
            folder_created,
            folder_reused
        )

    cur.execute("""
        INSERT INTO cases (
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
            notes,
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
            %s, %s, %s,
            'MIRRORED',
            %s,
            'Created by Advocate Diaries Sync v3'
        )
    """, (
        case.get("ad_case_id"),
        case.get("ad_client_id"),
        client_local_id,
        case_number,
        case_number,
        case.get("case_title"),
        client.get("client_name")
        or case.get("client_name"),
        mobile or None,
        case.get("case_type"),
        case.get("court_name"),
        case.get("judge_name"),
        case.get("opposite_party"),
        case.get("next_hearing"),
        case.get("next_hearing"),
        case.get("status"),
        case.get("purpose"),
        folder_id,
        folder_link,
        parse_timestamp(
            case.get(
                "ad_created_at"
            )
        ),
    ))

    return (
        "added",
        folder_created,
        folder_reused
    )


def repair_case_mobile_links(
    cur
) -> int:
    cur.execute("""
        UPDATE cases c
        SET
            mobile = COALESCE(
                NULLIF(
                    TRIM(
                        c.mobile
                    ),
                    ''
                ),
                NULLIF(
                    TRIM(
                        cl.mobile
                    ),
                    ''
                ),
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

        WHERE (
            c.client_id = cl.id
            OR (
                c.ad_client_id IS NOT NULL
                AND
                c.ad_client_id =
                    cl.ad_client_id
            )
            OR (
                LOWER(
                    TRIM(
                        c.client_name
                    )
                )
                =
                LOWER(
                    TRIM(
                        cl.client_name
                    )
                )
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
            OR c.ad_client_id IS NULL
        )
    """)

    return int(cur.rowcount)


def run_sync_v3() -> Dict[str, int]:
    access_token = login()

    raw_cases = fetch_all_cases(
        access_token
    )

    parsed_cases = [
        parse_case_payload(
            payload
        )
        for payload in raw_cases
    ]

    unique_client_ids = sorted({
        case["ad_client_id"]
        for case in parsed_cases
        if case.get("ad_client_id")
    })

    stats = {
        "cases_fetched": len(
            parsed_cases
        ),
        "unique_clients": len(
            unique_client_ids
        ),
        "clients_fetched": 0,
        "client_fetch_failed": 0,
        "clients_created": 0,
        "clients_updated": 0,
        "mobiles_imported": 0,
        "emails_imported": 0,
        "addresses_imported": 0,
        "cases_added": 0,
        "cases_updated": 0,
        "cases_skipped": 0,
        "folders_created": 0,
        "folders_reused": 0,
        "cases_repaired": 0,
    }

    client_cache = {}

    for client_id in unique_client_ids:
        try:
            raw_client = fetch_client_detail(
                access_token,
                client_id
            )

            client_cache[
                client_id
            ] = parse_client_payload(
                raw_client
            )

            stats[
                "clients_fetched"
            ] += 1

        except Exception as exc:
            client_cache[
                client_id
            ] = None

            stats[
                "client_fetch_failed"
            ] += 1

            print(
                "AD CLIENT FETCH FAILED: "
                f"{client_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    conn = psycopg2.connect(
        DATABASE_URL
    )

    cur = conn.cursor()

    try:
        ensure_schema(cur)

        local_client_ids = {}

        for client_id, client in (
            client_cache.items()
        ):
            if not client:
                continue

            local_id, client_stats = (
                upsert_client(
                    cur,
                    client
                )
            )

            local_client_ids[
                client_id
            ] = local_id

            for key, value in (
                client_stats.items()
            ):
                stats[key] += value

        for case in parsed_cases:
            ad_client_id = case.get(
                "ad_client_id"
            )

            client = client_cache.get(
                ad_client_id
            )

            if not client:
                client = {
                    "ad_client_id": (
                        ad_client_id
                    ),
                    "client_name": (
                        case.get(
                            "client_name"
                        )
                        or "Unknown Client"
                    ),
                    "mobile": "",
                    "email": "",
                    "address": "",
                }

                local_id, client_stats = (
                    upsert_client(
                        cur,
                        client
                    )
                )

                local_client_ids[
                    ad_client_id
                ] = local_id

                for key, value in (
                    client_stats.items()
                ):
                    stats[key] += value

            local_client_id = (
                local_client_ids.get(
                    ad_client_id
                )
            )

            action, created, reused = (
                upsert_case(
                    cur,
                    case,
                    client,
                    local_client_id
                )
            )

            if action == "added":
                stats[
                    "cases_added"
                ] += 1

            elif action == "updated":
                stats[
                    "cases_updated"
                ] += 1

            else:
                stats[
                    "cases_skipped"
                ] += 1

            if created:
                stats[
                    "folders_created"
                ] += 1

            if reused:
                stats[
                    "folders_reused"
                ] += 1

        stats[
            "cases_repaired"
        ] = repair_case_mobile_links(
            cur
        )

        conn.commit()

        return stats

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()
