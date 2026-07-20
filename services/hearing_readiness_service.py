from __future__ import annotations

import os
from datetime import date
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

READY_STATUSES = {"BROUGHT"}
EXCEPTION_STATUSES = {"NOT_FOUND", "NEEDS_ATTENTION"}


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _safe(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return text or fallback


def _owner_for_case(cur, case_number: str) -> str:
    queries = [
        "SELECT owner_name FROM case_ownership WHERE case_number=%s ORDER BY updated_at DESC NULLS LAST LIMIT 1",
        "SELECT assigned_to FROM cases WHERE case_number=%s LIMIT 1",
        "SELECT owner FROM cases WHERE case_number=%s LIMIT 1",
    ]
    for sql in queries:
        try:
            cur.execute(sql, (case_number,))
            row = cur.fetchone()
            if row:
                value = next(iter(dict(row).values()))
                if str(value or "").strip():
                    return str(value).strip()
        except Exception:
            cur.connection.rollback()
    return "Not assigned"


def _pending_works(cur, case_number: str) -> list[str]:
    try:
        cur.execute(
            """
            SELECT COALESCE(work_title,title,description,'Pending work') AS item
            FROM case_works
            WHERE case_number=%s
              AND UPPER(COALESCE(status,'PENDING')) NOT IN ('COMPLETED','COMPLETE','DONE','CLOSED','CANCELLED','VERIFIED')
            ORDER BY due_date NULLS LAST, id
            LIMIT 5
            """,
            (case_number,),
        )
        return [_safe(r["item"], "Pending work") for r in cur.fetchall()]
    except Exception:
        cur.connection.rollback()
        return []


def readiness_for_date(target: date) -> list[dict]:
    from services.role_intelligence_service import ensure_schema

    ensure_schema()
    with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM physical_file_assignments WHERE assignment_date=%s ORDER BY court,floor,room,id",
            (target,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        result = []
        for row in rows:
            case_number = _safe(row.get("case_number"), "Case number not entered")
            owner = _owner_for_case(cur, case_number)
            works = _pending_works(cur, case_number)
            checks = {
                "physical_file_selected": True,
                "physical_file_collected": str(row.get("status") or "SELECTED").upper() in READY_STATUSES,
                "hearing_purpose_verified": bool(str(row.get("purpose") or "").strip()),
                "advocate_assigned": owner.lower() != "not assigned",
                "pending_work_clear": not works,
            }
            passed = sum(1 for value in checks.values() if value)
            score = round((passed / len(checks)) * 100)
            exceptions = []
            status = str(row.get("status") or "SELECTED").upper()
            if status == "SELECTED":
                exceptions.append("Physical file not yet marked brought")
            elif status == "NOT_FOUND":
                exceptions.append("Physical file not found")
            elif status == "NEEDS_ATTENTION":
                exceptions.append("Physical file needs attention")
            if not checks["hearing_purpose_verified"]:
                exceptions.append("Hearing purpose not recorded")
            if not checks["advocate_assigned"]:
                exceptions.append("Advocate/owner not assigned")
            if works:
                exceptions.extend([f"Pending work: {item}" for item in works])
            if status in EXCEPTION_STATUSES:
                score = min(score, 40)
            readiness = "READY" if score == 100 else ("ATTENTION" if score >= 60 else "NOT READY")
            result.append({**row, "owner": owner, "pending_works": works, "checks": checks, "score": score, "readiness": readiness, "exceptions": exceptions})
        return result


def readiness_summary(target: date) -> dict:
    rows = readiness_for_date(target)
    total = len(rows)
    ready = sum(1 for r in rows if r["readiness"] == "READY")
    attention = sum(1 for r in rows if r["readiness"] == "ATTENTION")
    not_ready = sum(1 for r in rows if r["readiness"] == "NOT READY")
    brought = sum(1 for r in rows if str(r.get("status") or "").upper() == "BROUGHT")
    missing = sum(1 for r in rows if str(r.get("status") or "").upper() == "NOT_FOUND")
    score = round(sum(r["score"] for r in rows) / total) if total else 100
    reasons = []
    pending_collection = sum(1 for r in rows if str(r.get("status") or "SELECTED").upper() == "SELECTED")
    unassigned = sum(1 for r in rows if r["owner"].lower() == "not assigned")
    pending_work_cases = sum(1 for r in rows if r["pending_works"])
    if pending_collection:
        reasons.append(f"{pending_collection} selected file(s) not yet marked brought")
    if missing:
        reasons.append(f"{missing} file(s) marked not found")
    if unassigned:
        reasons.append(f"{unassigned} case(s) without an assigned owner")
    if pending_work_cases:
        reasons.append(f"{pending_work_cases} case(s) with pending work")
    return {"date": target, "total": total, "ready": ready, "attention": attention, "not_ready": not_ready, "brought": brought, "missing": missing, "score": score, "reasons": reasons, "rows": rows}
