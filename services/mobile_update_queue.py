from typing import Any, Dict, List

from services.communication_service import (
    get_db_connection,
    normalize_mobile,
)


def _valid_mobile(value: Any) -> str:
    try:
        return normalize_mobile(
            str(value or "")
        )
    except Exception:
        return ""


def get_mobile_update_queue(
    limit: int = 300
) -> List[Dict[str, Any]]:
    """
    Return one row per unique client requiring mobile-data correction.

    Cases are grouped by:
    1. ad_client_id
    2. local client_id
    3. normalized client name

    This prevents staff from seeing the same client once for every case.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                c.id,
                COALESCE(
                    NULLIF(TRIM(c.case_number), ''),
                    NULLIF(TRIM(c.case_id), ''),
                    'Case number not recorded'
                ) AS case_reference,
                c.client_name,
                c.client_id,
                c.ad_client_id,
                c.mobile AS case_mobile,
                c.case_title,
                c.status,
                COALESCE(
                    c.next_hearing,
                    c.hearing_date
                ) AS next_hearing,

                cl.id AS linked_client_id,
                cl.client_name AS linked_client_name,
                cl.mobile AS client_mobile,
                cl.whatsapp_number,
                cl.email,
                cl.address

            FROM cases c

            LEFT JOIN clients cl
                ON (
                    c.client_id = cl.id
                    OR (
                        c.ad_client_id IS NOT NULL
                        AND
                        c.ad_client_id = cl.ad_client_id
                    )
                )

            WHERE
                LOWER(
                    COALESCE(
                        c.status,
                        'pending'
                    )
                ) NOT IN (
                    'closed',
                    'disposed',
                    'decided',
                    'dismissed',
                    'withdrawn',
                    'settled',
                    'archived'
                )

            ORDER BY
                LOWER(
                    TRIM(
                        COALESCE(
                            cl.client_name,
                            c.client_name,
                            ''
                        )
                    )
                ),
                COALESCE(
                    c.next_hearing,
                    c.hearing_date
                ) ASC NULLS LAST,
                case_reference ASC
        """)

        rows = cur.fetchall()

        grouped = {}

        for row in rows:
            (
                case_db_id,
                case_reference,
                client_name,
                client_id,
                ad_client_id,
                case_mobile,
                case_title,
                status,
                next_hearing,
                linked_client_id,
                linked_client_name,
                client_mobile,
                whatsapp_number,
                email,
                address,
            ) = row

            resolved_mobile = (
                _valid_mobile(case_mobile)
                or _valid_mobile(client_mobile)
                or _valid_mobile(whatsapp_number)
            )

            if resolved_mobile:
                continue

            normalized_name = (
                str(
                    linked_client_name
                    or client_name
                    or "Unknown Client"
                )
                .strip()
                .lower()
            )

            if ad_client_id:
                group_key = (
                    "ad",
                    str(ad_client_id)
                )
            elif linked_client_id or client_id:
                group_key = (
                    "local",
                    str(
                        linked_client_id
                        or client_id
                    )
                )
            else:
                group_key = (
                    "name",
                    normalized_name
                )

            if group_key not in grouped:
                grouped[group_key] = {
                    "client_name": (
                        linked_client_name
                        or client_name
                        or "Unknown Client"
                    ),
                    "client_id": (
                        linked_client_id
                        or client_id
                    ),
                    "ad_client_id": ad_client_id,
                    "email": email or "",
                    "address": address or "",
                    "cases": [],
                }

            grouped[group_key][
                "cases"
            ].append({
                "case_db_id": case_db_id,
                "case_reference": case_reference,
                "case_title": case_title or "",
                "status": status or "pending",
                "next_hearing": next_hearing,
            })

        queue = list(
            grouped.values()
        )

        for item in queue:
            item["cases"].sort(
                key=lambda case: (
                    case.get("next_hearing")
                    is None,
                    str(
                        case.get(
                            "next_hearing"
                        )
                        or ""
                    ),
                    case.get(
                        "case_reference"
                    )
                    or "",
                )
            )

            item["active_case_count"] = len(
                item["cases"]
            )

            if not item.get("ad_client_id"):
                item["reason"] = (
                    "No Advocate Diaries client ID is linked."
                )
            else:
                item["reason"] = (
                    "Mobile number is missing in Advocate Diaries."
                )

        queue.sort(
            key=lambda item: (
                str(
                    item.get(
                        "client_name"
                    )
                    or ""
                ).lower(),
                str(
                    item.get(
                        "ad_client_id"
                    )
                    or ""
                ),
            )
        )

        return queue[:limit]

    finally:
        cur.close()
        conn.close()


def get_mobile_update_queue_summary() -> Dict[str, int]:
    queue = get_mobile_update_queue(
        limit=10000
    )

    clients_with_ad_id = sum(
        1
        for item in queue
        if item.get("ad_client_id")
    )

    clients_without_ad_id = (
        len(queue)
        - clients_with_ad_id
    )

    affected_cases = sum(
        item.get(
            "active_case_count",
            0
        )
        for item in queue
    )

    return {
        "clients_pending": len(queue),
        "affected_cases": affected_cases,
        "clients_with_ad_id": (
            clients_with_ad_id
        ),
        "clients_without_ad_id": (
            clients_without_ad_id
        ),
    }
