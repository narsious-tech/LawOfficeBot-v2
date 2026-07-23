"""Admin-only private loan ledger with reducing-balance monthly interest."""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

MONEY = Decimal("0.01")


def admin_ids() -> set[int]:
    preferred = os.getenv("ADMIN_USER_ID", "").strip()
    if preferred.lstrip("-").isdigit():
        return {int(preferred)}
    # Compatibility fallback only when the dedicated administrator ID is absent.
    values = (os.getenv("AI_ADMIN_USER_IDS", ""), os.getenv("ADMIN_CHAT_ID", ""))
    result: set[int] = set()
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                result.add(int(item))
    return result


def is_loan_admin(user_id: int | None) -> bool:
    allowed = admin_ids()
    return bool(user_id is not None and allowed and int(user_id) in allowed)


def parse_money(value: str) -> Decimal:
    try:
        amount = Decimal(value.replace(",", "").replace("₹", "").strip()).quantize(MONEY)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid positive amount.") from exc
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    return amount


def parse_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value.replace("%", "").strip()).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid monthly interest rate.") from exc
    if rate <= 0 or rate > 100:
        raise ValueError("Monthly rate must be greater than 0 and not more than 100%.")
    return rate


def parse_date(value: str) -> date:
    text = value.strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Use DD-MM-YYYY, for example 23-07-2026.")


def add_month(value: date, months: int = 1) -> date:
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def monthly_interest(loan: dict[str, Any]) -> Decimal:
    principal = Decimal(loan.get("outstanding_principal") or 0)
    rate = Decimal(loan.get("monthly_interest_rate") or 0)
    return (principal * rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)


def due_installments(next_due: date, as_of: date) -> int:
    count = 0
    cursor = next_due
    while cursor <= as_of and count < 1200:
        count += 1
        cursor = add_month(cursor)
    return count


def ensure_loan_schema() -> None:
    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-private-loan-ledger",
    )
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_loans (
                id BIGSERIAL PRIMARY KEY,
                account_number TEXT UNIQUE,
                borrower_name TEXT NOT NULL,
                borrower_phone TEXT,
                borrower_address TEXT,
                principal_amount NUMERIC(16,2) NOT NULL CHECK (principal_amount > 0),
                outstanding_principal NUMERIC(16,2) NOT NULL CHECK (outstanding_principal >= 0),
                monthly_interest_rate NUMERIC(9,4) NOT NULL CHECK (monthly_interest_rate > 0),
                calculation_method TEXT NOT NULL DEFAULT 'REDUCING_BALANCE',
                interest_timing TEXT NOT NULL DEFAULT 'MONTHLY_IN_ADVANCE',
                loan_date DATE NOT NULL,
                next_interest_due_date DATE NOT NULL,
                maturity_date DATE,
                guarantor_name TEXT,
                guarantor_phone TEXT,
                guarantor_address TEXT,
                security_details TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ,
                CONSTRAINT private_loans_status_chk
                    CHECK (status IN ('ACTIVE','CLOSED','DEFAULTED'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_loan_transactions (
                id BIGSERIAL PRIMARY KEY,
                loan_id BIGINT NOT NULL REFERENCES private_loans(id),
                transaction_date DATE NOT NULL DEFAULT CURRENT_DATE,
                transaction_type TEXT NOT NULL,
                amount NUMERIC(16,2) NOT NULL CHECK (amount > 0),
                payment_mode TEXT,
                reference_note TEXT,
                principal_before NUMERIC(16,2),
                principal_after NUMERIC(16,2),
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT private_loan_txn_type_chk
                    CHECK (transaction_type IN ('DISBURSEMENT','INTEREST_RECEIVED','PRINCIPAL_RECEIVED','CHARGE'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_loan_documents (
                id BIGSERIAL PRIMARY KEY,
                loan_id BIGINT NOT NULL REFERENCES private_loans(id),
                document_name TEXT NOT NULL,
                document_details TEXT,
                received_date DATE NOT NULL DEFAULT CURRENT_DATE,
                original_received BOOLEAN NOT NULL DEFAULT FALSE,
                drive_link TEXT,
                returned_at TIMESTAMPTZ,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_loan_audit (
                id BIGSERIAL PRIMARY KEY,
                loan_id BIGINT REFERENCES private_loans(id),
                action TEXT NOT NULL,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                actor_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_loan_reminder_log (
                id BIGSERIAL PRIMARY KEY,
                loan_id BIGINT NOT NULL REFERENCES private_loans(id),
                due_date DATE NOT NULL,
                alert_date DATE NOT NULL,
                alert_kind TEXT NOT NULL,
                sent_to BIGINT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(loan_id, due_date, alert_date, alert_kind, sent_to)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS private_loans_status_due_idx ON private_loans(status,next_interest_due_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS private_loan_txn_loan_date_idx ON private_loan_transactions(loan_id,transaction_date DESC,id DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS private_loan_docs_loan_idx ON private_loan_documents(loan_id,id DESC)")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def create_loan(
    *,
    borrower_name: str,
    borrower_phone: str | None,
    borrower_address: str | None,
    principal: Decimal,
    monthly_rate: Decimal,
    loan_date: date,
    next_due_date: date,
    maturity_date: date | None,
    guarantor_name: str | None,
    guarantor_phone: str | None,
    guarantor_address: str | None,
    security_details: str | None,
    notes: str | None,
    documents: list[str],
    actor_id: int,
) -> int:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO private_loans (
                    borrower_name,borrower_phone,borrower_address,principal_amount,outstanding_principal,
                    monthly_interest_rate,loan_date,next_interest_due_date,maturity_date,
                    guarantor_name,guarantor_phone,guarantor_address,security_details,notes,created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                borrower_name, borrower_phone, borrower_address, principal, principal, monthly_rate,
                loan_date, next_due_date, maturity_date, guarantor_name,
                guarantor_phone, guarantor_address, security_details, notes, actor_id,
            ))
            loan_id = int(cur.fetchone()["id"])
            account = f"LN-{loan_date.year}-{loan_id:05d}"
            cur.execute("UPDATE private_loans SET account_number=%s WHERE id=%s", (account, loan_id))
            cur.execute("""
                INSERT INTO private_loan_transactions (
                    loan_id,transaction_date,transaction_type,amount,payment_mode,
                    reference_note,principal_before,principal_after,created_by
                ) VALUES (%s,%s,'DISBURSEMENT',%s,'NOT_RECORDED','Loan disbursed',%s,%s,%s)
            """, (loan_id, loan_date, principal, Decimal("0"), principal, actor_id))
            for document in documents:
                cur.execute("""
                    INSERT INTO private_loan_documents
                        (loan_id,document_name,received_date,created_by)
                    VALUES (%s,%s,%s,%s)
                """, (loan_id, document, loan_date, actor_id))
            cur.execute("""
                INSERT INTO private_loan_audit(loan_id,action,details,actor_id)
                VALUES (%s,'LOAN_CREATED',jsonb_build_object(
                    'account_number',%s,'principal',%s,'monthly_rate',%s
                ),%s)
            """, (loan_id, account, str(principal), str(monthly_rate), actor_id))
        conn.commit()
        return loan_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_loans(status: str | None = "ACTIVE", limit: int = 30) -> list[dict[str, Any]]:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status:
                cur.execute("""
                    SELECT * FROM private_loans WHERE status=%s
                    ORDER BY next_interest_due_date,id LIMIT %s
                """, (status, max(1, min(limit, 100))))
            else:
                cur.execute("SELECT * FROM private_loans ORDER BY id DESC LIMIT %s", (max(1, min(limit, 100)),))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_loan(loan_id: int) -> dict[str, Any] | None:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM private_loans WHERE id=%s", (loan_id,))
            row = cur.fetchone()
            if not row:
                return None
            loan = dict(row)
            cur.execute("""
                SELECT * FROM private_loan_transactions
                WHERE loan_id=%s ORDER BY transaction_date DESC,id DESC LIMIT 50
            """, (loan_id,))
            loan["transactions"] = [dict(item) for item in cur.fetchall()]
            cur.execute("""
                SELECT * FROM private_loan_documents
                WHERE loan_id=%s AND returned_at IS NULL ORDER BY id
            """, (loan_id,))
            loan["documents"] = [dict(item) for item in cur.fetchall()]
            return loan
    finally:
        conn.close()


def record_payment(
    *,
    loan_id: int,
    payment_type: str,
    amount: Decimal,
    payment_date: date,
    payment_mode: str,
    note: str,
    actor_id: int,
) -> dict[str, Any]:
    if payment_type not in {"INTEREST_RECEIVED", "PRINCIPAL_RECEIVED"}:
        raise ValueError("Invalid loan payment type.")
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM private_loans WHERE id=%s FOR UPDATE", (loan_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Loan account not found.")
            loan = dict(row)
            before = Decimal(loan["outstanding_principal"])
            after = before
            next_due = loan["next_interest_due_date"]
            if payment_type == "PRINCIPAL_RECEIVED":
                if next_due <= payment_date:
                    raise ValueError(
                        "Record the interest due through the payment date before reducing principal."
                    )
                if amount > before:
                    raise ValueError("Principal receipt cannot exceed the outstanding principal.")
                after = (before - amount).quantize(MONEY)
            else:
                scheduled = monthly_interest(loan)
                if scheduled > 0:
                    if amount % scheduled != 0:
                        raise ValueError(
                            f"Interest receipt must be an exact monthly installment "
                            f"or multiple of {scheduled:.2f}."
                        )
                    cycles = int(amount // scheduled)
                    if cycles > 0:
                        next_due = add_month(next_due, cycles)
            status = "CLOSED" if after == 0 else loan["status"]
            cur.execute("""
                INSERT INTO private_loan_transactions (
                    loan_id,transaction_date,transaction_type,amount,payment_mode,
                    reference_note,principal_before,principal_after,created_by
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (loan_id, payment_date, payment_type, amount, payment_mode, note, before, after, actor_id))
            transaction_id = int(cur.fetchone()["id"])
            cur.execute("""
                UPDATE private_loans SET outstanding_principal=%s,
                    next_interest_due_date=%s,status=%s,updated_at=NOW(),
                    closed_at=CASE WHEN %s='CLOSED' THEN NOW() ELSE closed_at END
                WHERE id=%s
            """, (after, next_due, status, status, loan_id))
            cur.execute("""
                INSERT INTO private_loan_audit(loan_id,action,details,actor_id)
                VALUES (%s,'PAYMENT_RECORDED',jsonb_build_object(
                    'transaction_id',%s,'type',%s,'amount',%s,
                    'principal_before',%s,'principal_after',%s
                ),%s)
            """, (
                loan_id, transaction_id, payment_type, str(amount),
                str(before), str(after), actor_id,
            ))
        conn.commit()
        return get_loan(loan_id) or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_documents(loan_id: int, documents: list[str], actor_id: int) -> int:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            for document in documents:
                cur.execute("""
                    INSERT INTO private_loan_documents
                        (loan_id,document_name,received_date,created_by)
                    VALUES (%s,%s,CURRENT_DATE,%s)
                """, (loan_id, document, actor_id))
            cur.execute("""
                INSERT INTO private_loan_audit(loan_id,action,details,actor_id)
                VALUES (%s,'DOCUMENTS_ADDED',jsonb_build_object('count',%s),%s)
            """, (loan_id, len(documents), actor_id))
        conn.commit()
        return len(documents)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def interest_alerts(as_of: date | None = None) -> list[dict[str, Any]]:
    as_of = as_of or date.today()
    loans = list_loans("ACTIVE", 100)
    result = []
    for loan in loans:
        due = loan["next_interest_due_date"]
        days = (due - as_of).days
        if days > 3:
            continue
        item = dict(loan)
        item["days_to_due"] = days
        item["monthly_interest"] = monthly_interest(loan)
        item["installments_due"] = due_installments(due, as_of) if days <= 0 else 0
        item["interest_due"] = (
            item["monthly_interest"] * max(1, item["installments_due"])
            if days <= 0 else item["monthly_interest"]
        ).quantize(MONEY)
        item["alert_kind"] = "OVERDUE" if days < 0 else ("DUE_TODAY" if days == 0 else "ADVANCE")
        result.append(item)
    return result


def reminder_already_sent(loan_id: int, due_date: date, alert_date: date, kind: str, sent_to: int) -> bool:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM private_loan_reminder_log
                WHERE loan_id=%s AND due_date=%s AND alert_date=%s
                  AND alert_kind=%s AND sent_to=%s
            """, (loan_id, due_date, alert_date, kind, sent_to))
            return bool(cur.fetchone())
    finally:
        conn.close()


def mark_reminder_sent(loan_id: int, due_date: date, alert_date: date, kind: str, sent_to: int) -> None:
    ensure_loan_schema()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO private_loan_reminder_log
                    (loan_id,due_date,alert_date,alert_kind,sent_to)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (loan_id, due_date, alert_date, kind, sent_to))
        conn.commit()
    finally:
        conn.close()
