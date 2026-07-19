"""Restricted financial ledger service for LawOfficeBot v3 Sprint 8."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
ALLOWED_STAFF_NAMES = {
    item.strip().casefold()
    for item in os.getenv("LEDGER_ALLOWED_STAFF_NAMES", "Preet").split(",")
    if item.strip()
}


@dataclass(frozen=True)
class LedgerAccess:
    allowed: bool
    actor_name: str
    reason: str = ""


def ensure_ledger_schema() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS financial_ledger (
                id BIGSERIAL PRIMARY KEY,
                entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
                entry_type VARCHAR(12) NOT NULL,
                scope VARCHAR(20) NOT NULL,
                category VARCHAR(80) NOT NULL,
                amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
                description TEXT NOT NULL,
                case_id BIGINT,
                case_number TEXT,
                staff_name TEXT,
                payment_mode VARCHAR(30),
                created_by_telegram_id BIGINT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                deleted_at TIMESTAMPTZ,
                deleted_by_telegram_id BIGINT,
                CONSTRAINT financial_ledger_type_chk
                    CHECK (entry_type IN ('INCOME', 'EXPENSE')),
                CONSTRAINT financial_ledger_scope_chk
                    CHECK (scope IN ('PERSONAL', 'PROFESSIONAL', 'STAFF'))
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_financial_ledger_date
            ON financial_ledger(entry_date DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_financial_ledger_case
            ON financial_ledger(case_number)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_financial_ledger_active
            ON financial_ledger(is_deleted, entry_date DESC)
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def check_access(telegram_user_id: int) -> LedgerAccess:
    if ADMIN_USER_ID and str(telegram_user_id) == ADMIN_USER_ID:
        return LedgerAccess(True, "Ajay")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT staff_name
            FROM staff_accounts
            WHERE telegram_user_id = %s
              AND is_active = TRUE
            ORDER BY id DESC
            LIMIT 1
        """, (telegram_user_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        return LedgerAccess(False, "", "Telegram account is not linked to active staff.")

    name = str(row[0] or "").strip()
    if name.casefold() in ALLOWED_STAFF_NAMES:
        return LedgerAccess(True, name)
    return LedgerAccess(False, name, "Ledger access is restricted to Ajay and Preet.")


def parse_amount(value: str) -> Decimal:
    cleaned = value.replace(",", "").replace("₹", "").strip()
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid positive amount.") from exc
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    return amount


def add_entry(*, entry_type: str, scope: str, category: str, amount: Decimal,
              description: str, actor_id: int, actor_name: str,
              entry_date: date | None = None, case_id: int | None = None,
              case_number: str | None = None, staff_name: str | None = None,
              payment_mode: str | None = None) -> int:
    ensure_ledger_schema()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO financial_ledger (
                entry_date, entry_type, scope, category, amount, description,
                case_id, case_number, staff_name, payment_mode,
                created_by_telegram_id, created_by_name
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            entry_date or date.today(), entry_type, scope, category, amount,
            description.strip(), case_id, case_number, staff_name, payment_mode,
            actor_id, actor_name,
        ))
        entry_id = int(cur.fetchone()[0])
        conn.commit()
        return entry_id
    finally:
        cur.close()
        conn.close()


def soft_delete_entry(entry_id: int, actor_id: int) -> bool:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE financial_ledger
            SET is_deleted = TRUE,
                deleted_at = NOW(),
                deleted_by_telegram_id = %s,
                updated_at = NOW()
            WHERE id = %s AND is_deleted = FALSE
        """, (actor_id, entry_id))
        changed = cur.rowcount == 1
        conn.commit()
        return changed
    finally:
        cur.close()
        conn.close()


def ledger_summary(start_date: date, end_date: date, case_number: str | None = None) -> dict[str, Any]:
    ensure_ledger_schema()
    params: list[Any] = [start_date, end_date]
    case_sql = ""
    if case_number:
        case_sql = " AND LOWER(COALESCE(case_number,'')) = LOWER(%s)"
        params.append(case_number)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(f"""
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE entry_type='INCOME'),0) AS income,
                COALESCE(SUM(amount) FILTER (WHERE entry_type='EXPENSE'),0) AS expense,
                COALESCE(SUM(amount) FILTER (WHERE entry_type='EXPENSE' AND scope='PERSONAL'),0) AS personal_expense,
                COALESCE(SUM(amount) FILTER (WHERE entry_type='EXPENSE' AND scope='PROFESSIONAL'),0) AS professional_expense,
                COALESCE(SUM(amount) FILTER (WHERE entry_type='EXPENSE' AND scope='STAFF'),0) AS staff_expense,
                COUNT(*) AS entries
            FROM financial_ledger
            WHERE is_deleted = FALSE
              AND entry_date BETWEEN %s AND %s
              {case_sql}
        """, params)
        summary = dict(cur.fetchone() or {})
        cur.execute(f"""
            SELECT id, entry_date, entry_type, scope, category, amount,
                   description, case_number, staff_name, payment_mode,
                   created_by_name, created_at
            FROM financial_ledger
            WHERE is_deleted = FALSE
              AND entry_date BETWEEN %s AND %s
              {case_sql}
            ORDER BY entry_date DESC, id DESC
            LIMIT 30
        """, params)
        summary["rows"] = [dict(row) for row in cur.fetchall()]
        return summary
    finally:
        cur.close()
        conn.close()
