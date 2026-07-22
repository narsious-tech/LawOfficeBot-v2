from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL


def _clean(value: Any, default: str = "Not recorded") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class CaseMatch:
    db_id: int
    case_number: str
    case_title: str
    client_name: str
    next_hearing: str


@dataclass(frozen=True)
class CaseIntelligenceContext:
    case: dict[str, Any]
    ownership: dict[str, Any] | None
    works: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    staff: list[dict[str, Any]]
    physical_file: dict[str, Any] | None
    unavailable_sources: tuple[str, ...]

    def _source_state(self, label: str, value: Any) -> str:
        if label in self.unavailable_sources:
            return "NOT_CHECKED_OR_UNAVAILABLE"
        if value is None or value == [] or value == {}:
            return "CHECKED_NO_RECORDS_FOUND"
        return "CHECKED_RECORDS_FOUND"

    def to_prompt(self) -> str:
        drive_link = self.case.get("drive_folder_link") or self.case.get("drive_folder_id")
        fee_values = {
            key: self.case.get(key)
            for key in ("fee_agreed", "advance_received", "fee_outstanding")
            if key in self.case
        }
        payload = {
            "verified_office_context": {
                "case": self.case,
                "case_ownership": self.ownership,
                "open_works": self.works,
                "recent_timeline": self.timeline,
                "documents": self.documents,
                "case_staff": self.staff,
                "physical_file": self.physical_file,
            },
            "source_status": {
                "master_case": "CHECKED_RECORD_FOUND",
                "case_ownership": self._source_state("case ownership", self.ownership),
                "case_works": self._source_state("case works", self.works),
                "case_timeline": (
                    "NOT_CHECKED_OR_UNAVAILABLE"
                    if any(item in self.unavailable_sources for item in ("case timeline", "hearing timeline"))
                    else self._source_state("case timeline", self.timeline)
                ),
                "local_document_metadata": self._source_state("documents", self.documents),
                "google_drive_folder_link": "RECORDED" if drive_link else "NOT_RECORDED",
                "google_drive_folder_contents": "NOT_INSPECTED",
                "case_staff": self._source_state("case staff", self.staff),
                "physical_file_status": self._source_state("physical-file status", self.physical_file),
                "fee_fields": (
                    "RECORDED" if any(value not in (None, "", 0, "0", "0.00") for value in fee_values.values())
                    else "NOT_RECORDED_IN_MASTER_CASE"
                ),
            },
            "data_quality": {
                "unavailable_sources": list(self.unavailable_sources),
                "interpretation_rules": [
                    "CHECKED_NO_RECORDS_FOUND means no record was found in that named local source only.",
                    "NOT_CHECKED_OR_UNAVAILABLE must never be described as none, absent, missing, or not uploaded.",
                    "Google Drive folder contents were not inspected even when a folder link is recorded.",
                    "A blank fee field means not recorded in the master case, not that no fee agreement exists.",
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value)


@dataclass(frozen=True)
class HearingDayContext:
    target_date: date
    cases: list[dict[str, Any]]
    unavailable_sources: tuple[str, ...]

    def to_prompt(self) -> str:
        payload = {
            "hearing_date": self.target_date.isoformat(),
            "verified_office_context": {"hearings": self.cases},
            "source_status": {
                "master_cases": "CHECKED_RECORDS_FOUND" if self.cases else "CHECKED_NO_RECORDS_FOUND",
                "google_drive_folder_contents": "NOT_INSPECTED",
                "unavailable_sources": list(self.unavailable_sources),
            },
            "interpretation_rules": [
                "Each hearing entry belongs to one distinct case; never combine cases.",
                "Only listed fields and connected local records are verified office facts.",
                "A Drive link does not mean the folder contents were inspected.",
                "An unavailable source must not be described as empty or missing.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value)


class OfficeKnowledgeService:
    """Read-only, bounded office knowledge for Ajay AI.

    Every optional module is loaded independently. Schema drift therefore
    becomes a visible data-quality notice instead of breaking the AI request.
    """

    def _connect(self):
        return psycopg2.connect(
            DATABASE_URL,
            connect_timeout=15,
            application_name="law-office-ai-case-intelligence",
        )

    @staticmethod
    def _first_value(row: Any) -> Any:
        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()), None)
        try:
            return row[0]
        except (KeyError, IndexError, TypeError):
            return None

    @staticmethod
    def _table_exists(cur, table: str) -> bool:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        row = cur.fetchone()
        return bool(OfficeKnowledgeService._first_value(row))

    @staticmethod
    def _columns(cur, table: str) -> set[str]:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s""",
            (table,),
        )
        return {
            str(value)
            for row in cur.fetchall()
            if (value := OfficeKnowledgeService._first_value(row)) is not None
        }

    @staticmethod
    def _pick(columns: set[str], *names: str, default: str = "NULL") -> str:
        return next((name for name in names if name in columns), default)

    def search_cases(self, query: str = "", limit: int = 8) -> list[CaseMatch]:
        query = query.strip()
        term = f"%{query}%"
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                columns = self._columns(cur, "cases")
                if not columns:
                    return []
                number = self._pick(columns, "case_number", "case_id")
                title = self._pick(columns, "case_title", "title")
                client = self._pick(columns, "client_name")
                opposite = self._pick(columns, "opposite_party")
                next_date = self._pick(columns, "next_hearing", "next_hearing_date", "hearing_date")
                searchable = [name for name in (number, title, client, opposite) if name != "NULL"]
                where = " OR ".join(f"COALESCE({name}::text,'') ILIKE %s" for name in searchable)
                params: list[Any] = [term] * len(searchable)
                if not query or not searchable:
                    where = "TRUE"
                    params = []
                params.append(max(1, min(limit, 20)))
                cur.execute(
                    f"""SELECT id, {number} AS case_number, {title} AS case_title,
                               {client} AS client_name, {next_date} AS next_hearing
                        FROM cases WHERE {where}
                        ORDER BY id DESC LIMIT %s""",
                    tuple(params),
                )
                return [
                    CaseMatch(
                        db_id=int(row["id"]),
                        case_number=_clean(row.get("case_number"), "Case number not recorded"),
                        case_title=_clean(row.get("case_title"), "Title not recorded"),
                        client_name=_clean(row.get("client_name")),
                        next_hearing=_clean(row.get("next_hearing")),
                    )
                    for row in cur.fetchall()
                ]
        finally:
            conn.close()

    @staticmethod
    def _as_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        for candidate in (text[:10], text):
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y"):
                try:
                    return datetime.strptime(candidate, fmt).date()
                except ValueError:
                    continue
        return None

    def build_hearing_day_context(self, target_date: date, limit: int = 20) -> HearingDayContext:
        """Build a bounded, read-only preparation context for one hearing date."""
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                columns = self._columns(cur, "cases")
                date_columns = [name for name in ("next_hearing", "next_hearing_date", "hearing_date") if name in columns]
                if not date_columns:
                    return HearingDayContext(target_date, [], ("case hearing date",))
                where = " OR ".join(f"{name} IS NOT NULL" for name in date_columns)
                cur.execute(f"SELECT * FROM cases WHERE {where} ORDER BY id DESC LIMIT 2000")
                rows = [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

        matched: list[dict[str, Any]] = []
        unavailable: set[str] = set()
        for row in rows:
            if not any(self._as_date(row.get(name)) == target_date for name in date_columns):
                continue
            context = self.build_case_context(int(row["id"]))
            if not context:
                continue
            unavailable.update(context.unavailable_sources)
            case = context.case
            matched.append({
                "case_record_id": case.get("id"),
                "case_number": case.get("case_number") or case.get("case_id"),
                "case_title": case.get("case_title") or case.get("title"),
                "client_name": case.get("client_name"),
                "opposite_party": case.get("opposite_party"),
                "case_type": case.get("case_type"),
                "court": case.get("court_name"),
                "judge": case.get("judge_name"),
                "hearing_date": target_date.isoformat(),
                "purpose": case.get("next_purpose") or case.get("purpose"),
                "status": case.get("status"),
                "client_verification_status": case.get("client_verification_status"),
                "drive_folder_link": case.get("drive_folder_link"),
                "ownership": context.ownership,
                "open_works": context.works[:8],
                "recent_timeline": context.timeline[:5],
                "document_metadata": context.documents[:10],
                "case_staff": context.staff[:8],
                "physical_file": context.physical_file,
            })
            if len(matched) >= max(1, min(limit, 30)):
                break
        matched.sort(key=lambda item: (
            str(item.get("court") or ""), str(item.get("judge") or ""), str(item.get("case_number") or "")
        ))
        return HearingDayContext(target_date, matched, tuple(sorted(unavailable)))

    def build_case_context(self, case_db_id: int) -> CaseIntelligenceContext | None:
        conn = self._connect()
        unavailable: list[str] = []
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM cases WHERE id=%s LIMIT 1", (case_db_id,))
                raw_case = cur.fetchone()
                if not raw_case:
                    return None
                case = {key: _json_value(value) for key, value in dict(raw_case).items()
                        if key not in {"password", "token", "access_token", "refresh_token"}}
                identifiers = [
                    str(case.get(name)).strip()
                    for name in ("case_number", "case_id", "ad_case_id")
                    if case.get(name) and str(case.get(name)).strip()
                ] or ["__unmatched_case__"]

                ownership = self._load_ownership(cur, identifiers, unavailable)
                works = self._load_works(cur, case_db_id, identifiers, unavailable)
                timeline = self._load_timeline(cur, case_db_id, identifiers, unavailable)
                documents = self._load_documents(cur, case_db_id, identifiers, unavailable)
                staff = self._load_staff(cur, identifiers, unavailable)
                physical_file = self._load_physical_file(cur, identifiers, unavailable)
                return CaseIntelligenceContext(
                    case=case,
                    ownership=ownership,
                    works=works,
                    timeline=timeline,
                    documents=documents,
                    staff=staff,
                    physical_file=physical_file,
                    unavailable_sources=tuple(unavailable),
                )
        finally:
            conn.close()

    def _load_ownership(self, cur, ids: list[str], unavailable: list[str]):
        if not self._table_exists(cur, "case_ownership"):
            unavailable.append("case ownership")
            return None
        columns = self._columns(cur, "case_ownership")
        selected = [
            name for name in (
                "owner_staff", "assignment_mode", "source_floor", "source_court",
                "source_judge", "manual_override", "assigned_at", "updated_at"
            ) if name in columns
        ]
        if not selected or "case_number" not in columns:
            unavailable.append("case ownership")
            return None
        active_filter = "AND COALESCE(active,TRUE)=TRUE" if "active" in columns else ""
        try:
            cur.execute(
                f"""SELECT {', '.join(selected)} FROM case_ownership
                   WHERE case_number=ANY(%s) {active_filter}
                   ORDER BY id DESC LIMIT 1""",
                (ids,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except psycopg2.Error:
            conn = cur.connection
            conn.rollback()
            unavailable.append("case ownership")
            return None

    def _load_works(self, cur, case_id: int, ids: list[str], unavailable: list[str]):
        if not self._table_exists(cur, "case_works"):
            unavailable.append("case works")
            return []
        try:
            cur.execute(
                """SELECT title, details, assigned_to, due_date, priority, status, source
                   FROM case_works
                   WHERE (case_record_id=%s OR case_number=ANY(%s))
                     AND UPPER(COALESCE(status,'PENDING')) NOT IN ('COMPLETED','CLOSED')
                   ORDER BY due_date NULLS LAST, id DESC LIMIT 25""",
                (case_id, ids),
            )
            return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error:
            cur.connection.rollback()
            unavailable.append("case works")
            return []

    def _load_timeline(self, cur, case_id: int, ids: list[str], unavailable: list[str]):
        events: list[dict[str, Any]] = []
        if self._table_exists(cur, "case_hearing_timeline"):
            try:
                cur.execute(
                    """SELECT event_date, event_type, status, outcome, next_hearing_date,
                              next_purpose, preparation, court_name, judge_name
                       FROM case_hearing_timeline
                       WHERE case_record_id=%s OR case_number=ANY(%s)
                       ORDER BY COALESCE(event_date,created_at::date) DESC, id DESC LIMIT 12""",
                    (case_id, ids),
                )
                events.extend(dict(row) for row in cur.fetchall())
            except psycopg2.Error:
                cur.connection.rollback()
                unavailable.append("hearing timeline")
        elif self._table_exists(cur, "client_timeline"):
            try:
                cur.execute(
                    """SELECT event_at, event_title, event_details, event_category, event_status
                       FROM client_timeline
                       WHERE case_number=ANY(%s) OR case_id=ANY(%s)
                       ORDER BY COALESCE(event_at,created_at) DESC, id DESC LIMIT 12""",
                    (ids, ids),
                )
                events.extend(dict(row) for row in cur.fetchall())
            except psycopg2.Error:
                cur.connection.rollback()
                unavailable.append("case timeline")
        else:
            unavailable.append("case timeline")
        return events[:12]

    def _load_documents(self, cur, case_id: int, ids: list[str], unavailable: list[str]):
        if not self._table_exists(cur, "documents"):
            unavailable.append("documents")
            return []
        columns = self._columns(cur, "documents")
        name = self._pick(columns, "file_name", "document_name", "title", "name")
        category = self._pick(columns, "category", "document_type", "type")
        created = self._pick(columns, "uploaded_at", "created_at", "date")
        clauses = []
        params: list[Any] = []
        if "case_record_id" in columns:
            clauses.append("case_record_id=%s")
            params.append(case_id)
        if "case_number" in columns:
            clauses.append("case_number=ANY(%s)")
            params.append(ids)
        if "case_id" in columns:
            clauses.append("case_id::text=ANY(%s)")
            params.append(ids)
        if not clauses:
            unavailable.append("documents linkage")
            return []
        try:
            cur.execute(
                f"""SELECT {name} AS name, {category} AS category, {created} AS recorded_at
                    FROM documents WHERE {' OR '.join(clauses)}
                    ORDER BY id DESC LIMIT 20""",
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error:
            cur.connection.rollback()
            unavailable.append("documents")
            return []

    def _load_staff(self, cur, ids: list[str], unavailable: list[str]):
        if not self._table_exists(cur, "case_responsibility"):
            unavailable.append("case staff")
            return []
        try:
            cur.execute(
                """SELECT staff_name, responsibility FROM case_responsibility
                   WHERE case_number=ANY(%s) ORDER BY id LIMIT 20""",
                (ids,),
            )
            return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error:
            cur.connection.rollback()
            unavailable.append("case staff")
            return []

    def _load_physical_file(self, cur, ids: list[str], unavailable: list[str]):
        if not self._table_exists(cur, "physical_file_assignments"):
            unavailable.append("physical-file status")
            return None
        columns = self._columns(cur, "physical_file_assignments")
        number = self._pick(columns, "case_number")
        if number == "NULL":
            unavailable.append("physical-file linkage")
            return None
        select_cols = [name for name in ("status", "hearing_date", "assigned_to", "updated_at", "case_title") if name in columns]
        try:
            cur.execute(
                f"""SELECT {', '.join(select_cols) if select_cols else 'id'}
                    FROM physical_file_assignments WHERE case_number=ANY(%s)
                    ORDER BY id DESC LIMIT 1""",
                (ids,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except psycopg2.Error:
            cur.connection.rollback()
            unavailable.append("physical-file status")
            return None

    def case_snapshot(self, case_reference: str) -> str:
        """Backward-compatible bounded snapshot used by AILIP v1.0 callers."""
        matches = self.search_cases(case_reference, limit=1)
        if not matches:
            return f"No matching local case found for: {case_reference.strip()}"
        context = self.build_case_context(matches[0].db_id)
        return context.to_prompt() if context else "The selected case is no longer available."
