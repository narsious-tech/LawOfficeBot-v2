from typing import Any, Dict, List, Optional

from services.communication_service import (
    get_db_connection,
    normalize_mobile,
)


def ensure_mobile_audit_schema():
    """
    No new table is required for the first version.
    This function is retained so startup integration remains consistent.
    """
    return True


def _normalize_candidate(value: Any) -> str:
    try:
        return normalize_mobile(
            str(value or "")
        )
    except Exception:
        return ""


def get_missing_mobile_report(
    limit: int = 200
) -> List[Dict[str, Any]]:
    """
    Return active/upcoming cases for which no usable mobile can be resolved.

    The report distinguishes:
    - no linked client
    - linked client has no mobile
    - invalid local mobile
    - client record missing
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
                c.mobile AS case_mobile,
                c.client_id,
                c.ad_client_id,
                c.case_title,
                c.status,
                COALESCE(
                    c.next_hearing,
                    c.hearing_date
                ) AS next_hearing,

                cl.id AS linked_client_id,
                cl.ad_client_id AS linked_ad_client_id,
                cl.mobile AS client_mobile,
                cl.whatsapp_number,
                cl.client_name AS linked_client_name

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
                COALESCE(
                    c.next_hearing,
                    c.hearing_date
                ) ASC NULLS LAST,
                case_reference ASC

            LIMIT %s
        """, (
            limit,
        ))

        rows = cur.fetchall()

        report = []

        for row in rows:
            (
                case_db_id,
                case_reference,
                client_name,
                case_mobile,
                client_id,
                ad_client_id,
                case_title,
                status,
                next_hearing,
                linked_client_id,
                linked_ad_client_id,
                client_mobile,
                whatsapp_number,
                linked_client_name,
            ) = row

            resolved_mobile = (
                _normalize_candidate(
                    case_mobile
                )
                or _normalize_candidate(
                    client_mobile
                )
                or _normalize_candidate(
                    whatsapp_number
                )
            )

            if resolved_mobile:
                continue

            reasons = []

            if not client_id and not ad_client_id:
                reasons.append(
                    "Case is not linked to a client."
                )

            elif not linked_client_id:
                reasons.append(
                    "Linked client record could not be found locally."
                )

            else:
                if not client_mobile and not whatsapp_number:
                    reasons.append(
                        "Client record has no phone number."
                    )
                else:
                    reasons.append(
                        "Client phone exists but is not a valid Indian mobile number."
                    )

            if case_mobile:
                reasons.append(
                    "Case mobile exists but is invalid."
                )
            else:
                reasons.append(
                    "Case mobile is blank."
                )

            report.append({
                "case_db_id": case_db_id,
                "case_reference": case_reference,
                "client_name": (
                    client_name
                    or linked_client_name
                    or "-"
                ),
                "case_title": case_title or "",
                "status": status or "pending",
                "next_hearing": next_hearing,
                "client_id": client_id,
                "ad_client_id": ad_client_id,
                "linked_client_id": linked_client_id,
                "reasons": reasons,
            })

        return report

    finally:
        cur.close()
        conn.close()


def repair_missing_mobiles() -> Dict[str, int]:
    """
    Repair missing case mobiles from linked client records.

    Repair order:
    1. Existing client_id
    2. Matching ad_client_id
    3. Unique normalized client name

    Only valid normalized Indian mobile numbers are copied.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    stats = {
        "cases_checked": 0,
        "case_links_repaired": 0,
        "mobiles_repaired": 0,
        "still_missing": 0,
    }

    try:
        cur.execute("""
            SELECT
                c.id,
                c.client_id,
                c.ad_client_id,
                c.client_name,
                c.mobile
            FROM cases c
            WHERE
                TRIM(
                    COALESCE(
                        c.mobile,
                        ''
                    )
                ) = ''
                OR c.client_id IS NULL
                OR c.ad_client_id IS NULL
            ORDER BY c.id ASC
        """)

        cases = cur.fetchall()

        for (
            case_id,
            client_id,
            ad_client_id,
            client_name,
            case_mobile
        ) in cases:
            stats["cases_checked"] += 1

            linked_client = None

            if client_id:
                cur.execute("""
                    SELECT
                        id,
                        ad_client_id,
                        mobile,
                        whatsapp_number
                    FROM clients
                    WHERE id = %s
                    LIMIT 1
                """, (
                    client_id,
                ))

                linked_client = cur.fetchone()

            if (
                not linked_client
                and ad_client_id
            ):
                cur.execute("""
                    SELECT
                        id,
                        ad_client_id,
                        mobile,
                        whatsapp_number
                    FROM clients
                    WHERE ad_client_id = %s
                    ORDER BY id ASC
                    LIMIT 1
                """, (
                    ad_client_id,
                ))

                linked_client = cur.fetchone()

            if (
                not linked_client
                and client_name
            ):
                cur.execute("""
                    SELECT
                        id,
                        ad_client_id,
                        mobile,
                        whatsapp_number
                    FROM clients
                    WHERE
                        LOWER(TRIM(client_name))
                        =
                        LOWER(TRIM(%s))
                    ORDER BY id ASC
                    LIMIT 2
                """, (
                    client_name,
                ))

                name_rows = cur.fetchall()

                if len(name_rows) == 1:
                    linked_client = (
                        name_rows[0]
                    )

            if not linked_client:
                stats["still_missing"] += 1
                continue

            (
                resolved_client_id,
                resolved_ad_client_id,
                client_mobile,
                whatsapp_number,
            ) = linked_client

            mobile = (
                _normalize_candidate(
                    client_mobile
                )
                or _normalize_candidate(
                    whatsapp_number
                )
            )

            link_changed = (
                client_id != resolved_client_id
                or (
                    not ad_client_id
                    and resolved_ad_client_id
                )
            )

            mobile_changed = (
                not _normalize_candidate(
                    case_mobile
                )
                and bool(mobile)
            )

            cur.execute("""
                UPDATE cases
                SET
                    client_id = COALESCE(
                        client_id,
                        %s
                    ),
                    ad_client_id = COALESCE(
                        ad_client_id,
                        %s
                    ),
                    mobile = CASE
                        WHEN
                            TRIM(
                                COALESCE(
                                    mobile,
                                    ''
                                )
                            ) = ''
                            AND %s <> ''
                        THEN %s
                        ELSE mobile
                    END
                WHERE id = %s
            """, (
                resolved_client_id,
                resolved_ad_client_id,
                mobile,
                mobile,
                case_id,
            ))

            if link_changed:
                stats[
                    "case_links_repaired"
                ] += 1

            if mobile_changed:
                stats[
                    "mobiles_repaired"
                ] += 1

            if not mobile:
                stats[
                    "still_missing"
                ] += 1

        conn.commit()

        return stats

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def get_mobile_audit_summary() -> Dict[str, int]:
    """
    Return overall mobile-data health statistics.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                COUNT(*) AS total_cases,

                COUNT(*) FILTER (
                    WHERE TRIM(
                        COALESCE(
                            mobile,
                            ''
                        )
                    ) <> ''
                ) AS cases_with_mobile,

                COUNT(*) FILTER (
                    WHERE TRIM(
                        COALESCE(
                            mobile,
                            ''
                        )
                    ) = ''
                ) AS cases_without_mobile,

                COUNT(*) FILTER (
                    WHERE client_id IS NULL
                ) AS cases_without_client_link,

                COUNT(*) FILTER (
                    WHERE ad_client_id IS NULL
                ) AS cases_without_ad_client_link

            FROM cases
        """)

        row = cur.fetchone()

        return {
            "total_cases": int(row[0]),
            "cases_with_mobile": int(row[1]),
            "cases_without_mobile": int(row[2]),
            "cases_without_client_link": int(row[3]),
            "cases_without_ad_client_link": int(row[4]),
        }

    finally:
        cur.close()
        conn.close()
