"""Verification-first reconciliation of staff dates against delayed eCourts data."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from config import DATABASE_URL
from services.ecourts_backup_service import ensure_ecourts_schema
from services.ecourts_date_rules import as_date as _as_date
from services.ecourts_date_rules import classify_dates

_TERMINAL_CASE_STATUSES = ("DISPOSED", "CLOSED", "DECIDED", "ARCHIVED", "INACTIVE")
_TERMINAL_STATUS_SQL = ",".join(f"'{value}'" for value in _TERMINAL_CASE_STATUSES)


def _conn():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="law-office-ecourts-date-verification",
    )


def ensure_date_verification_schema() -> None:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_date_verifications (
                id BIGSERIAL PRIMARY KEY,
                local_case_pk TEXT NOT NULL,
                cino TEXT NOT NULL,
                display_case_number TEXT,
                staff_next_date DATE,
                ecourts_next_date DATE,
                staff_last_date DATE,
                ecourts_last_date DATE,
                ecourts_purpose TEXT,
                source_sync_run_id BIGINT,
                verification_status TEXT NOT NULL,
                status_message TEXT,
                alert_sent_at TIMESTAMPTZ,
                reviewed_by BIGINT,
                reviewed_at TIMESTAMPTZ,
                review_decision TEXT,
                ad_sync_status TEXT,
                ad_sync_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(local_case_pk, cino)
            )
        """)
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_date_source TEXT")
        cur.execute(
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS "
            "next_date_verification_status TEXT"
        )
        cur.execute(
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_date_verified_at TIMESTAMPTZ"
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _case_columns(cur) -> set[str]:
    cur.execute("""
        SELECT column_name AS column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='cases'
    """)
    columns = set()
    for row in cur.fetchall():
        value = row.get("column_name") if isinstance(row, dict) else row[0]
        if value:
            columns.add(str(value))
    return columns


def _case_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _refresh_linked_ad_dates() -> tuple[int, list[str]]:
    """Refresh linked Office OS dates from live Advocate Diaries case data.

    This deliberately avoids the full client/Drive synchronization.  If the
    live read fails, the caller fails closed rather than comparing eCourts to a
    stale local date and generating a false conflict.
    """
    from services.ad_sync_v3 import fetch_all_cases, login, parse_case_payload

    raw_cases = fetch_all_cases(login())
    live_dates: dict[str, date] = {}
    live_ids: dict[str, date] = {}
    for raw in raw_cases:
        parsed = parse_case_payload(raw)
        next_date = _as_date(parsed.get("next_hearing"))
        if not next_date:
            continue
        number_key = _case_key(parsed.get("case_number"))
        ad_case_id = str(parsed.get("ad_case_id") or "").strip()
        if number_key:
            live_dates[number_key] = next_date
        if ad_case_id:
            live_ids[ad_case_id] = next_date

    if raw_cases and not live_dates and not live_ids:
        raise RuntimeError(
            "Advocate Diaries returned cases but no readable live next-hearing dates."
        )

    conn = _conn()
    cur = conn.cursor()
    updated = 0
    verified_local_ids: list[str] = []
    try:
        cur.execute("""
            SELECT c.id::text, c.ad_case_id,
                   COALESCE(c.case_number, c.case_id) case_number
            FROM cases c
            JOIN ecourts_case_links l
              ON l.local_case_pk=c.id::text AND l.link_status='APPROVED'
        """)
        rows = cur.fetchall()
        for local_case_pk, ad_case_id, case_number in rows:
            next_date = live_ids.get(str(ad_case_id or "").strip())
            if next_date is None:
                next_date = live_dates.get(_case_key(case_number))
            if next_date is None:
                continue
            verified_local_ids.append(str(local_case_pk))
            cur.execute("""
                UPDATE cases
                   SET next_hearing=%s,
                       ad_sync_status='MIRRORED',
                       ad_sync_message='Live date refreshed before eCourts verification'
                 WHERE id::text=%s
                   AND next_hearing IS DISTINCT FROM %s
            """, (next_date.isoformat(), str(local_case_pk), next_date.isoformat()))
            updated += int(cur.rowcount)
        conn.commit()
        return updated, sorted(set(verified_local_ids))
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _consumer_case_clause(columns: set[str], alias: str = "c") -> str:
    """Identify matters that belong to e-Jagriti, not ordinary eCourts."""
    predicates = []
    number_col = next((x for x in ("case_number", "case_id") if x in columns), None)
    if number_col:
        predicates.append(
            f"UPPER(TRIM(COALESCE({alias}.{number_col}::text,''))) "
            r"~ '^(CC|COMI)[/-]'"
        )
    for name in ("case_type", "type", "court", "court_name"):
        if name in columns:
            predicates.append(
                f"UPPER(COALESCE({alias}.{name}::text,'')) "
                r"~ '(CONSUMER|DCDRC|SCDRC|NCDRC)'"
            )
    return f"({' OR '.join(predicates)})" if predicates else "FALSE"


def reconcile_date_verifications(sync_run_id: int | None = None) -> dict[str, int]:
    """Compare approved links. Never overwrite a staff-entered date."""
    ensure_date_verification_schema()
    ad_dates_refreshed, live_ad_case_ids = _refresh_linked_ad_dates()
    if not live_ad_case_ids:
        raise RuntimeError(
            "No approved CNR-linked case could be verified against a live Advocate Diaries date."
        )
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    counts = {
        "verified": 0,
        "conflicts": 0,
        "awaiting_ecourts": 0,
        "no_staff_date": 0,
        "historical_stale": 0,
        "disposed_skipped": 0,
        "consumer_routed": 0,
        "ad_dates_refreshed": ad_dates_refreshed,
    }
    try:
        columns = _case_columns(cur)
        cur.execute("""
            UPDATE ecourts_date_verifications
               SET verification_status='AWAITING_AD_REFRESH',
                   status_message='Live Advocate Diaries date was not available; conflict suppressed.',
                   alert_sent_at=NULL,
                   review_decision=NULL,
                   reviewed_by=NULL,
                   reviewed_at=NULL,
                   updated_at=NOW()
             WHERE verification_status='DATE_CONFLICT'
               AND NOT (local_case_pk=ANY(%s))
        """, (live_ad_case_ids,))
        if "status" in columns:
            cur.execute(f"""
                UPDATE ecourts_date_verifications v
                   SET verification_status='DISPOSED_SKIPPED',
                       status_message='Disposed/closed case excluded from date verification.',
                       alert_sent_at=NULL,
                       review_decision=NULL,
                       reviewed_by=NULL,
                       reviewed_at=NULL,
                       updated_at=NOW()
                  FROM cases c
                 WHERE c.id::text=v.local_case_pk
                   AND UPPER(TRIM(COALESCE(c.status,'')))
                       IN ({_TERMINAL_STATUS_SQL})
                   AND v.verification_status <> 'DISPOSED_SKIPPED'
            """)
            counts["disposed_skipped"] = int(cur.rowcount)
            cur.execute(f"""
                UPDATE cases
                   SET next_date_verification_status='DISPOSED_SKIPPED',
                       next_date_verified_at=NULL
                 WHERE UPPER(TRIM(COALESCE(status,'')))
                       IN ({_TERMINAL_STATUS_SQL})
            """)
        next_expr = next(
            (f"c.{name}" for name in ("next_hearing", "hearing_date") if name in columns),
            "NULL",
        )
        last_expr = next(
            (f"c.{name}" for name in ("last_hearing_date", "last_hearing") if name in columns),
            "NULL",
        )
        case_expr = next(
            (f"c.{name}" for name in ("case_number", "case_id") if name in columns),
            "c.id::text",
        )
        consumer_clause = _consumer_case_clause(columns)
        cur.execute(f"""
            UPDATE ecourts_date_verifications v
               SET verification_status='CONSUMER_EJAGRITI',
                   status_message='Consumer commission matter routed to e-Jagriti.',
                   alert_sent_at=NULL,
                   review_decision=NULL,
                   reviewed_by=NULL,
                   reviewed_at=NULL,
                   updated_at=NOW()
              FROM cases c
             WHERE c.id::text=v.local_case_pk
               AND {consumer_clause}
               AND v.verification_status <> 'CONSUMER_EJAGRITI'
        """)
        counts["consumer_routed"] = int(cur.rowcount)
        if "next_date_verification_status" in columns:
            cur.execute(f"""
                UPDATE cases c
                   SET next_date_verification_status='CONSUMER_EJAGRITI',
                       next_date_verified_at=NULL
                 WHERE {consumer_clause}
            """)
        active_status_clause = (
            "AND UPPER(TRIM(COALESCE(c.status,'OPEN'))) "
            f"NOT IN ({_TERMINAL_STATUS_SQL})"
            if "status" in columns else ""
        )
        cur.execute(f"""
            SELECT l.local_case_pk, l.cino, {case_expr} display_case_number,
                   {next_expr} staff_next_date, {last_expr} staff_last_date,
                   b.next_hearing_date ecourts_next_date,
                   b.last_hearing_date ecourts_last_date,
                   b.purpose_name ecourts_purpose,
                   b.last_sync_run_id source_sync_run_id
            FROM ecourts_case_links l
            JOIN cases c ON c.id::text=l.local_case_pk
            JOIN ecourts_backup_records b ON b.cino=l.cino
            WHERE l.link_status='APPROVED'
              {active_status_clause}
              AND NOT ({consumer_clause})
              AND l.local_case_pk=ANY(%s)
              AND (%s IS NULL OR b.last_sync_run_id=%s)
        """, (live_ad_case_ids, sync_run_id, sync_run_id))
        rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            status, message = classify_dates(
                row.get("staff_next_date"),
                row.get("ecourts_next_date"),
                row.get("staff_last_date"),
                row.get("ecourts_last_date"),
            )
            bucket = {
                "VERIFIED": "verified",
                "DATE_CONFLICT": "conflicts",
                "AWAITING_ECOURTS": "awaiting_ecourts",
                "NO_STAFF_DATE": "no_staff_date",
                "HISTORICAL_STALE": "historical_stale",
            }[status]
            counts[bucket] += 1
            cur.execute("""
                INSERT INTO ecourts_date_verifications (
                    local_case_pk,cino,display_case_number,staff_next_date,
                    ecourts_next_date,staff_last_date,ecourts_last_date,
                    ecourts_purpose,source_sync_run_id,verification_status,
                    status_message
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (local_case_pk,cino) DO UPDATE SET
                    display_case_number=EXCLUDED.display_case_number,
                    staff_next_date=EXCLUDED.staff_next_date,
                    ecourts_next_date=EXCLUDED.ecourts_next_date,
                    staff_last_date=EXCLUDED.staff_last_date,
                    ecourts_last_date=EXCLUDED.ecourts_last_date,
                    ecourts_purpose=EXCLUDED.ecourts_purpose,
                    source_sync_run_id=EXCLUDED.source_sync_run_id,
                    verification_status=EXCLUDED.verification_status,
                    status_message=EXCLUDED.status_message,
                    alert_sent_at=CASE
                        WHEN ecourts_date_verifications.staff_next_date
                             IS DISTINCT FROM EXCLUDED.staff_next_date
                          OR ecourts_date_verifications.ecourts_next_date
                             IS DISTINCT FROM EXCLUDED.ecourts_next_date
                        THEN NULL ELSE ecourts_date_verifications.alert_sent_at END,
                    reviewed_by=CASE
                        WHEN ecourts_date_verifications.staff_next_date
                             IS DISTINCT FROM EXCLUDED.staff_next_date
                          OR ecourts_date_verifications.ecourts_next_date
                             IS DISTINCT FROM EXCLUDED.ecourts_next_date
                        THEN NULL ELSE ecourts_date_verifications.reviewed_by END,
                    reviewed_at=CASE
                        WHEN ecourts_date_verifications.staff_next_date
                             IS DISTINCT FROM EXCLUDED.staff_next_date
                          OR ecourts_date_verifications.ecourts_next_date
                             IS DISTINCT FROM EXCLUDED.ecourts_next_date
                        THEN NULL ELSE ecourts_date_verifications.reviewed_at END,
                    review_decision=CASE
                        WHEN ecourts_date_verifications.staff_next_date
                             IS DISTINCT FROM EXCLUDED.staff_next_date
                          OR ecourts_date_verifications.ecourts_next_date
                             IS DISTINCT FROM EXCLUDED.ecourts_next_date
                        THEN NULL ELSE ecourts_date_verifications.review_decision END,
                    updated_at=NOW()
            """, (
                row["local_case_pk"], row["cino"], row["display_case_number"],
                _as_date(row.get("staff_next_date")),
                _as_date(row.get("ecourts_next_date")),
                _as_date(row.get("staff_last_date")),
                _as_date(row.get("ecourts_last_date")),
                row.get("ecourts_purpose"), row.get("source_sync_run_id"),
                status, message,
            ))
            verified_at = "NOW()" if status == "VERIFIED" else "NULL"
            cur.execute(f"""
                UPDATE cases SET next_date_verification_status=%s,
                    next_date_verified_at={verified_at}
                WHERE id::text=%s
            """, (status, row["local_case_pk"]))
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def list_date_conflicts(limit: int = 20, unalerted_only: bool = False) -> list[dict]:
    ensure_date_verification_schema()
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(f"""
            SELECT v.*,
                   COALESCE(
                       NULLIF(TRIM(c.case_title), ''),
                       NULLIF(CONCAT_WS(
                           ' vs ', NULLIF(TRIM(b.petitioner_name), ''),
                           NULLIF(TRIM(b.respondent_name), '')
                       ), '')
                   ) case_title
            FROM ecourts_date_verifications v
            JOIN cases c ON c.id::text=v.local_case_pk
            LEFT JOIN ecourts_backup_records b ON b.cino=v.cino
            WHERE v.verification_status='DATE_CONFLICT'
              AND UPPER(TRIM(COALESCE(c.status,'OPEN')))
                  NOT IN ({_TERMINAL_STATUS_SQL})
              AND (%s=FALSE OR alert_sent_at IS NULL)
            ORDER BY v.updated_at, v.id LIMIT %s
        """, (bool(unalerted_only), max(1, min(int(limit), 100))))
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def verification_summary() -> dict[str, int]:
    ensure_date_verification_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT verification_status, COUNT(*)
            FROM ecourts_date_verifications GROUP BY verification_status
        """)
        result = {str(row[0]).lower(): int(row[1]) for row in cur.fetchall()}
        result["total"] = sum(result.values())
        return result
    finally:
        cur.close()
        conn.close()


def mark_conflicts_alerted(ids: list[int]) -> None:
    if not ids:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE ecourts_date_verifications SET alert_sent_at=NOW() "
            "WHERE id=ANY(%s)",
            ([int(value) for value in ids],),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _invalidate_old_operational_items(
    cur,
    *,
    case_pk: str,
    case_number: str,
    old_date: date | None,
) -> dict[str, int]:
    result = {"reminders_cancelled": 0, "file_selections_removed": 0}
    if not old_date:
        return result

    cur.execute(
        "SELECT to_regclass('public.hearing_reminder_queue') AS relation"
    )
    if cur.fetchone().get("relation"):
        cur.execute(
            """
            UPDATE hearing_reminder_queue
               SET queue_status='CANCELLED',
                   cancelled_at=NOW(),
                   error_message='Replaced by administrator-approved eCourts date.'
             WHERE hearing_date=%s
               AND queue_status IN ('PENDING','APPROVED','READY','READY_FOR_REVIEW')
               AND (
                    case_db_id::text=%s
                    OR LOWER(TRIM(COALESCE(case_number,''))) =
                       LOWER(TRIM(COALESCE(%s,'')))
               )
            """,
            (old_date, case_pk, case_number),
        )
        result["reminders_cancelled"] = cur.rowcount

    cur.execute(
        "SELECT to_regclass('public.physical_file_assignments') AS relation"
    )
    if cur.fetchone().get("relation"):
        cur.execute(
            """
            DELETE FROM physical_file_assignments
             WHERE assignment_date=%s
               AND status='SELECTED'
               AND LOWER(TRIM(COALESCE(case_number,''))) =
                   LOWER(TRIM(COALESCE(%s,'')))
            """,
            (old_date, case_number),
        )
        result["file_selections_removed"] = cur.rowcount

    return result


def review_date_conflict(
    verification_id: int, decision: str, actor_id: int
) -> dict[str, Any]:
    """Apply an administrator decision and sync AD only when eCourts is accepted."""
    decision = str(decision or "").strip().upper()
    if decision not in {"ACCEPT_ECOURTS", "KEEP_STAFF", "REVIEW_LATER"}:
        raise ValueError("Invalid date-conflict decision.")
    ensure_date_verification_schema()
    downstream = {"reminders_cancelled": 0, "file_selections_removed": 0}
    conn = _conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM ecourts_date_verifications WHERE id=%s FOR UPDATE",
            (int(verification_id),),
        )
        item = cur.fetchone()
        if not item:
            raise ValueError("Date verification record was not found.")
        item = dict(item)
        if item["verification_status"] != "DATE_CONFLICT":
            raise ValueError("This date conflict is no longer pending.")
        if decision == "REVIEW_LATER":
            cur.execute("""
                UPDATE ecourts_date_verifications
                SET review_decision='REVIEW_LATER', reviewed_by=%s,
                    reviewed_at=NOW(), alert_sent_at=NULL, updated_at=NOW()
                WHERE id=%s
            """, (int(actor_id), int(verification_id)))
            conn.commit()
            item.update({"decision": decision, "ad_sync_status": "NOT_REQUIRED"})
            return item
        if decision == "KEEP_STAFF":
            cur.execute("""
                UPDATE ecourts_date_verifications
                SET verification_status='STAFF_CONFIRMED',
                    review_decision='KEEP_STAFF', reviewed_by=%s,
                    reviewed_at=NOW(), updated_at=NOW()
                WHERE id=%s
            """, (int(actor_id), int(verification_id)))
            cur.execute("""
                UPDATE cases SET next_date_verification_status='STAFF_CONFIRMED',
                    next_date_source='STAFF_CONFIRMED'
                WHERE id::text=%s
            """, (item["local_case_pk"],))
            message = "Staff date retained; Office OS and Advocate Diaries were not changed."
        else:
            columns = _case_columns(cur)
            assignments = []
            values = []
            for name in ("next_hearing", "hearing_date"):
                if name in columns:
                    assignments.append(f"{name}=%s")
                    values.append(item["ecourts_next_date"])
            if "next_purpose" in columns and item.get("ecourts_purpose"):
                assignments.append("next_purpose=%s")
                values.append(item["ecourts_purpose"])
            assignments.extend([
                "next_date_verification_status='ECOURTS_ACCEPTED'",
                "next_date_source='ECOURTS_ADMIN_APPROVED'",
                "next_date_verified_at=NOW()",
                "ecourts_last_synced_at=NOW()",
            ])
            values.append(item["local_case_pk"])
            cur.execute(
                f"UPDATE cases SET {', '.join(assignments)} WHERE id::text=%s",
                values,
            )
            if cur.rowcount != 1:
                raise ValueError("Linked Office OS case could not be updated.")
            downstream = _invalidate_old_operational_items(
                cur,
                case_pk=item["local_case_pk"],
                case_number=item.get("display_case_number") or "",
                old_date=item.get("staff_next_date"),
            )
            cur.execute("""
                UPDATE ecourts_date_verifications
                SET staff_next_date=ecourts_next_date,
                    verification_status='ECOURTS_ACCEPTED',
                    review_decision='ACCEPT_ECOURTS', reviewed_by=%s,
                    reviewed_at=NOW(), updated_at=NOW()
                WHERE id=%s
            """, (int(actor_id), int(verification_id)))
            cur.execute("""
                INSERT INTO ecourts_preparation_queue (
                    local_case_pk,cino,source_sync_run_id,source_change_ids,
                    next_hearing_date,purpose_name,order_status
                ) VALUES (%s,%s,%s,'{}',%s,%s,'AWAITING_PDF')
                ON CONFLICT (local_case_pk,cino,source_sync_run_id)
                DO UPDATE SET next_hearing_date=EXCLUDED.next_hearing_date,
                    purpose_name=EXCLUDED.purpose_name,
                    queue_status='READY_FOR_REVIEW'
            """, (
                item["local_case_pk"], item["cino"], item["source_sync_run_id"],
                item["ecourts_next_date"], item.get("ecourts_purpose"),
            ))
            message = "eCourts date applied to Office OS; Advocate Diaries sync requested."
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit (
                action,local_case_pk,cino,details,actor_id
            ) VALUES ('DATE_VERIFICATION_DECISION',%s,%s,%s,%s)
        """, (
            item["local_case_pk"], item["cino"],
            Json({
                "decision": decision,
                "staff_date": str(item.get("staff_next_date") or ""),
                "ecourts_date": str(item.get("ecourts_next_date") or ""),
                "message": message,
                **downstream,
            }),
            int(actor_id),
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    ad_result = {"status": "NOT_REQUIRED", "message": ""}
    if decision == "ACCEPT_ECOURTS":
        from services.ecourts_orchestration_service import sync_approved_case_to_ad
        ad_result = sync_approved_case_to_ad(
            int(item["source_sync_run_id"]), item["cino"], int(actor_id)
        )
        conn = _conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE ecourts_date_verifications
                SET ad_sync_status=%s, ad_sync_message=%s, updated_at=NOW()
                WHERE id=%s
            """, (
                ad_result.get("status"), ad_result.get("message"),
                int(verification_id),
            ))
            conn.commit()
        finally:
            cur.close()
            conn.close()
    item.update({
        "decision": decision,
        "message": message,
        "ad_sync_status": ad_result.get("status"),
        "ad_sync_message": ad_result.get("message"),
        **downstream,
    })
    return item
