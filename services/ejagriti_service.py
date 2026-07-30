"""Administrator-reviewed e-Jagriti bridge for consumer commission matters."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _columns(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s", (table,)
    )
    columns: set[str] = set()
    for row in cur.fetchall():
        # This helper is used with both ordinary cursors (tuple rows) and
        # RealDictCursor (named rows). Numeric access on RealDictRow raises
        # KeyError: 0 and previously broke /ejagritilink.
        value = row.get("column_name") if hasattr(row, "get") else row[0]
        if value:
            columns.add(str(value))
    return columns


def ensure_schema() -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ejagriti_case_links (
            id BIGSERIAL PRIMARY KEY,
            local_case_pk BIGINT NOT NULL UNIQUE,
            filing_reference TEXT,
            ejagriti_case_number TEXT,
            commission TEXT,
            link_status TEXT NOT NULL DEFAULT 'ACTIVE',
            linked_by BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS ejagriti_snapshots (
            id BIGSERIAL PRIMARY KEY,
            local_case_pk BIGINT NOT NULL,
            filing_reference TEXT,
            last_hearing_date DATE,
            next_hearing_date DATE,
            purpose TEXT,
            stage TEXT,
            history_count INTEGER,
            source_url TEXT,
            verified_by BIGINT,
            verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS ejagriti_date_reviews (
            id BIGSERIAL PRIMARY KEY,
            snapshot_id BIGINT NOT NULL UNIQUE,
            local_case_pk BIGINT NOT NULL,
            local_last_date DATE,
            local_next_date DATE,
            ejagriti_last_date DATE,
            ejagriti_next_date DATE,
            purpose TEXT,
            review_status TEXT NOT NULL DEFAULT 'PENDING',
            decision_by BIGINT,
            decision_at TIMESTAMPTZ,
            decision_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS ejagriti_orders (
            id BIGSERIAL PRIMARY KEY,
            local_case_pk BIGINT NOT NULL,
            order_date DATE,
            telegram_file_id TEXT,
            filename TEXT,
            drive_file_id TEXT,
            drive_url TEXT,
            uploaded_by BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)


def _case_fields(cur) -> dict[str, str | None]:
    cols = _columns(cur, "cases")
    pick = lambda *names: next((n for n in names if n in cols), None)
    return {
        "pk": pick("id", "case_id"),
        "number": pick("case_number", "case_no", "case_id"),
        "title": pick("case_title", "title"),
        "client": pick("client_name", "client"),
        "court": pick("court", "court_name"),
        "type": pick("case_type", "type"),
        "status": pick("status", "case_status"),
        "next": pick("next_hearing", "hearing_date", "next_hearing_date"),
        "last": pick("last_hearing_date", "last_hearing"),
        "purpose": pick("next_purpose", "purpose", "hearing_purpose"),
    }


def resolve_case(search: str) -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        f = _case_fields(cur)
        if not f["pk"] or not f["number"]:
            raise RuntimeError("The cases table does not expose a usable case identifier.")
        fields = [v for v in f.values() if v]
        select = ", ".join(f'"{x}"' for x in dict.fromkeys(fields))
        predicates, params = [], []
        if search.strip().isdigit():
            predicates.append(f'"{f["pk"]}"=%s')
            params.append(int(search.strip()))
        for name in (f["number"], f["title"], f["client"]):
            if name:
                predicates.append(f'LOWER(COALESCE("{name}"::text,\'\')) LIKE %s')
                params.append(f"%{search.strip().lower()}%")
        cur.execute(
            f'SELECT {select} FROM cases WHERE {" OR ".join(predicates)} '
            f'ORDER BY CASE WHEN LOWER(COALESCE("{f["number"]}"::text,\'\'))=%s THEN 0 ELSE 1 END LIMIT 2',
            [*params, search.strip().lower()],
        )
        rows = cur.fetchall()
        if not rows:
            raise LookupError("No Office OS case matched that reference.")
        row = dict(rows[0])
        return {
            "id": row.get(f["pk"]), "number": row.get(f["number"]),
            "title": row.get(f["title"]) if f["title"] else None,
            "client": row.get(f["client"]) if f["client"] else None,
            "court": row.get(f["court"]) if f["court"] else None,
            "type": row.get(f["type"]) if f["type"] else None,
            "status": row.get(f["status"]) if f["status"] else None,
            "next": row.get(f["next"]) if f["next"] else None,
            "last": row.get(f["last"]) if f["last"] else None,
            "purpose": row.get(f["purpose"]) if f["purpose"] else None,
        }


def link_case(case_search: str, filing_ref: str, ej_case_no: str, commission: str, user_id: int) -> dict:
    case = resolve_case(case_search)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ejagriti_case_links
              (local_case_pk, filing_reference, ejagriti_case_number, commission, linked_by)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (local_case_pk) DO UPDATE SET
              filing_reference=EXCLUDED.filing_reference,
              ejagriti_case_number=EXCLUDED.ejagriti_case_number,
              commission=EXCLUDED.commission, link_status='ACTIVE',
              linked_by=EXCLUDED.linked_by, updated_at=NOW()
        """, (case["id"], filing_ref, ej_case_no, commission, user_id))
    return case


def record_snapshot(case_search: str, last_date: date | None, next_date: date | None,
                    purpose: str, stage: str, history_count: int | None, user_id: int) -> dict:
    case = resolve_case(case_search)
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT filing_reference FROM ejagriti_case_links WHERE local_case_pk=%s", (case["id"],))
        link = cur.fetchone()
        if not link:
            raise RuntimeError("Link this consumer case with /ejagritilink first.")
        cur.execute("""
            INSERT INTO ejagriti_snapshots
              (local_case_pk, filing_reference, last_hearing_date, next_hearing_date,
               purpose, stage, history_count, source_url, verified_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'https://e-jagriti.gov.in/complete-case-details',%s)
            RETURNING id
        """, (case["id"], link["filing_reference"], last_date, next_date,
              purpose or None, stage or None, history_count, user_id))
        snapshot_id = cur.fetchone()["id"]
        cur.execute("""
            INSERT INTO ejagriti_date_reviews
              (snapshot_id, local_case_pk, local_last_date, local_next_date,
               ejagriti_last_date, ejagriti_next_date, purpose)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (snapshot_id, case["id"], case["last"], case["next"], last_date, next_date, purpose or None))
        review = dict(cur.fetchone())
    return {"case": case, "review": review}


def pending_reviews(limit: int = 20) -> list[dict]:
    ensure_schema()
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        f = _case_fields(cur)
        cur.execute(f"""
            SELECT r.*, c."{f['number']}" AS case_number,
                   {('c."' + f['title'] + '"') if f['title'] else 'NULL'} AS case_title,
                   l.ejagriti_case_number, l.commission
            FROM ejagriti_date_reviews r
            JOIN cases c ON c."{f['pk']}"=r.local_case_pk
            LEFT JOIN ejagriti_case_links l ON l.local_case_pk=r.local_case_pk
            WHERE r.review_status='PENDING'
            ORDER BY r.created_at LIMIT %s
        """, (limit,))
        return [dict(x) for x in cur.fetchall()]


def review_decision(review_id: int, decision: str, user_id: int) -> dict:
    ensure_schema()
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM ejagriti_date_reviews WHERE id=%s FOR UPDATE", (review_id,))
        item = cur.fetchone()
        if not item or item["review_status"] != "PENDING":
            raise LookupError("This e-Jagriti review is no longer pending.")
        f = _case_fields(cur)
        if decision == "ACCEPT_EJAGRITI":
            updates, values = [], []
            for col, val in ((f["last"], item["ejagriti_last_date"]),
                             (f["next"], item["ejagriti_next_date"]),
                             (f["purpose"], item["purpose"])):
                if col and val is not None:
                    updates.append(f'"{col}"=%s')
                    values.append(val)
            if updates:
                cur.execute(
                    f'UPDATE cases SET {", ".join(updates)} WHERE "{f["pk"]}"=%s',
                    [*values, item["local_case_pk"]],
                )
            status = "APPLIED"
            note = "e-Jagriti values applied to Office OS; Advocate Diaries was not changed automatically."
        elif decision == "KEEP_LOCAL":
            status, note = "KEPT_LOCAL", "Office OS values retained."
        else:
            # Keep deferred items visible in the pending queue.
            status, note = "PENDING", "Deferred for later review."
        cur.execute("""
            UPDATE ejagriti_date_reviews SET review_status=%s, decision_by=%s,
              decision_at=NOW(), decision_note=%s WHERE id=%s RETURNING *
        """, (status, user_id, note, review_id))
        return dict(cur.fetchone())


def dashboard_counts() -> dict[str, int]:
    ensure_schema()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ejagriti_case_links WHERE link_status='ACTIVE'")
        linked = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ejagriti_date_reviews WHERE review_status='PENDING'")
        pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ejagriti_orders")
        orders = cur.fetchone()[0]
    return {"linked": linked, "pending": pending, "orders": orders}


def save_order_record(case_search: str, order_date: date | None, telegram_file_id: str,
                      filename: str, drive_file_id: str, drive_url: str, user_id: int) -> dict:
    case = resolve_case(case_search)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ejagriti_orders
              (local_case_pk, order_date, telegram_file_id, filename,
               drive_file_id, drive_url, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (case["id"], order_date, telegram_file_id, filename,
              drive_file_id, drive_url, user_id))
    return case
