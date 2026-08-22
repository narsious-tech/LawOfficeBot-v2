"""Read-only eCourts app backup import and Office OS reconciliation."""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_values
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from config import DATABASE_URL
from utils.drive import get_drive_service, ROOT_FOLDER_ID

logger = logging.getLogger(__name__)

DISTRICT_BACKUP_ID = os.getenv(
    "ECOURTS_DISTRICT_BACKUP_FILE_ID",
    "10SEwFvszQQSv45156isuZ1-4bWVoX4Wo",
).strip()
HIGH_COURT_BACKUP_ID = os.getenv(
    "ECOURTS_HIGH_COURT_BACKUP_FILE_ID",
    "1qHpzr3yWKHEd5yrhs6ljUaa1_vwoqaQu",
).strip()


def _conn():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="law-office-ecourts-reconciliation",
    )


def ensure_ecourts_schema() -> None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_backup_sync_runs (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'RUNNING',
                district_count INTEGER NOT NULL DEFAULT 0,
                high_court_count INTEGER NOT NULL DEFAULT 0,
                matched_count INTEGER NOT NULL DEFAULT 0,
                possible_count INTEGER NOT NULL DEFAULT 0,
                office_only_count INTEGER NOT NULL DEFAULT 0,
                backup_only_count INTEGER NOT NULL DEFAULT 0,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                triggered_by BIGINT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_backup_records (
                cino TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                case_type TEXT,
                registration_number TEXT,
                registration_year INTEGER,
                display_case_number TEXT,
                raw_case_number TEXT,
                petitioner_name TEXT,
                respondent_name TEXT,
                establishment_name TEXT,
                establishment_code TEXT,
                state_name TEXT,
                district_name TEXT,
                court_designation TEXT,
                last_hearing_date DATE,
                next_hearing_date DATE,
                decision_date DATE,
                purpose_name TEXT,
                disposal_name TEXT,
                note TEXT,
                raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_sync_run_id BIGINT REFERENCES ecourts_backup_sync_runs(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_case_links (
                id BIGSERIAL PRIMARY KEY,
                local_case_pk TEXT NOT NULL,
                local_case_number TEXT,
                cino TEXT NOT NULL REFERENCES ecourts_backup_records(cino),
                match_method TEXT NOT NULL,
                confidence NUMERIC(6,5) NOT NULL DEFAULT 1,
                link_status TEXT NOT NULL DEFAULT 'APPROVED',
                approved_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(local_case_pk),
                UNIQUE(cino)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_reconciliation_audit (
                id BIGSERIAL PRIMARY KEY,
                action TEXT NOT NULL,
                local_case_pk TEXT,
                cino TEXT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                actor_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_match_rejections (
                id BIGSERIAL PRIMARY KEY,
                local_case_pk TEXT NOT NULL,
                cino TEXT NOT NULL,
                rejected_by BIGINT,
                rejected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                reason TEXT,
                UNIQUE(local_case_pk, cino)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_case_changes (
                id BIGSERIAL PRIMARY KEY,
                sync_run_id BIGINT REFERENCES ecourts_backup_sync_runs(id),
                cino TEXT NOT NULL,
                local_case_pk TEXT,
                display_case_number TEXT,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                severity TEXT NOT NULL DEFAULT 'INFO',
                detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                alerted_at TIMESTAMPTZ,
                UNIQUE(sync_run_id, cino, field_name)
            )
        """)
        cur.execute("""
            ALTER TABLE ecourts_case_changes
            ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'PENDING'
        """)
        cur.execute("""
            ALTER TABLE ecourts_case_changes
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ
        """)
        cur.execute("""
            ALTER TABLE ecourts_case_changes
            ADD COLUMN IF NOT EXISTS reviewed_by BIGINT
        """)
        cur.execute("""
            ALTER TABLE ecourts_case_changes
            ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ
        """)
        cur.execute("""
            ALTER TABLE ecourts_case_changes
            ADD COLUMN IF NOT EXISTS apply_message TEXT
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_backup_case_number
            ON ecourts_backup_records(display_case_number)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_backup_last_run
            ON ecourts_backup_records(last_sync_run_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_changes_detected
            ON ecourts_case_changes(detected_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_changes_review
            ON ecourts_case_changes(review_status, severity, id DESC)
        """)
        # Retire historical false positives such as "Consideration" versus
        # "CONSIDERATION". This changes only the review queue/audit status;
        # Office OS and Advocate Diaries values are never modified here.
        cur.execute("""
            UPDATE ecourts_case_changes
            SET review_status='IGNORED_COSMETIC',
                reviewed_at=COALESCE(reviewed_at, NOW()),
                apply_message=COALESCE(
                    apply_message,
                    'Ignored automatically: cosmetic text formatting only.'
                )
            WHERE review_status='PENDING'
              AND field_name IN (
                  'purpose_name', 'court_designation', 'disposal_name'
              )
              AND LOWER(REGEXP_REPLACE(
                    COALESCE(old_value, ''), '[^[:alnum:]]+', '', 'g'
                  )) = LOWER(REGEXP_REPLACE(
                    COALESCE(new_value, ''), '[^[:alnum:]]+', '', 'g'
                  ))
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ecourts_preparation_queue (
                id BIGSERIAL PRIMARY KEY,
                local_case_pk TEXT NOT NULL,
                cino TEXT NOT NULL,
                source_sync_run_id BIGINT,
                source_change_ids BIGINT[] NOT NULL DEFAULT '{}',
                next_hearing_date DATE,
                purpose_name TEXT,
                order_status TEXT NOT NULL DEFAULT 'AWAITING_PDF',
                queue_status TEXT NOT NULL DEFAULT 'READY_FOR_REVIEW',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ,
                UNIQUE(local_case_pk, cino, source_sync_run_id)
            )
        """)
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ecourts_cnr TEXT")
        cur.execute(
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS ecourts_last_synced_at TIMESTAMPTZ"
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _download_text(file_id: str) -> str:
    if not file_id:
        return ""
    drive = get_drive_service()
    if drive is None:
        raise RuntimeError("Google Drive is not connected.")
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue().decode("utf-8-sig")


def parse_backup(text: str, source_kind: str) -> list[dict[str, Any]]:
    """Parse the eCourts app's list-of-JSON-strings backup format."""
    value = json.loads(text)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("eCourts backup must contain a JSON list.")
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                continue
        if not isinstance(item, dict):
            continue
        cino = str(item.get("cino") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{16}", cino):
            continue
        case_type = str(item.get("type_name") or "").strip().upper()
        reg_no = str(item.get("reg_no") or "").strip()
        reg_year = _integer(item.get("reg_year"))
        display = (
            f"{case_type}/{reg_no}/{reg_year}"
            if case_type and reg_no and reg_year
            else ""
        )
        records.append({
            "cino": cino,
            "source_kind": source_kind,
            "case_type": case_type,
            "registration_number": reg_no,
            "registration_year": reg_year,
            "display_case_number": display,
            "raw_case_number": str(item.get("case_no") or "").strip(),
            "petitioner_name": _clean(item.get("petparty_name")),
            "respondent_name": _clean(item.get("resparty_name")),
            "establishment_name": _clean(item.get("establishment_name")),
            "establishment_code": _clean(item.get("establishment_code")),
            "state_name": _clean(item.get("state_name")),
            "district_name": _clean(item.get("district_name")),
            "court_designation": _clean(item.get("court_no_desg_name")),
            "last_hearing_date": _date(item.get("date_last_list")),
            "next_hearing_date": _date(item.get("date_next_list")),
            "decision_date": _date(item.get("date_of_decision")),
            "purpose_name": _clean(item.get("purpose_name")),
            "disposal_name": _clean(item.get("disp_name")),
            "note": _clean(item.get("note")),
            "raw_payload": item,
        })
    return records


def _backup_freshness(record: dict[str, Any], position: int) -> tuple:
    """Return a stable ordering key for duplicate CNR backup rows.

    eCourts backups can contain an older and a refreshed copy of the same CNR.
    Prefer an explicit ``updated`` timestamp when present, then the row carrying
    the most useful hearing/disposal information, and finally the later source
    row.  The final position tie-breaker keeps the result deterministic.
    """
    payload = record.get("raw_payload")
    updated = payload.get("updated") if isinstance(payload, dict) else None
    updated_epoch = 0.0
    if updated not in (None, ""):
        text = str(updated).strip()
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            updated_epoch = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            # Some eCourts exports use a numeric Unix timestamp.
            try:
                updated_epoch = float(text)
                if updated_epoch > 10_000_000_000:
                    updated_epoch /= 1000.0
            except (TypeError, ValueError, OverflowError):
                updated_epoch = 0.0
    useful_fields = sum(
        record.get(field) not in (None, "")
        for field in (
            "last_hearing_date", "next_hearing_date", "decision_date",
            "purpose_name", "disposal_name", "court_designation",
            "petitioner_name", "respondent_name",
        )
    )
    return (updated_epoch, useful_fields, position)


def deduplicate_backup_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Keep exactly one deterministic, freshest record for every CNR."""
    selected: dict[str, tuple[tuple, dict[str, Any]]] = {}
    for position, record in enumerate(records):
        cino = str(record.get("cino") or "").strip().upper()
        if not cino:
            continue
        freshness = _backup_freshness(record, position)
        current = selected.get(cino)
        if current is None or freshness > current[0]:
            selected[cino] = (freshness, record)
    unique = [value[1] for value in selected.values()]
    return unique, max(0, len(records) - len(unique))


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


_COSMETIC_TEXT_FIELDS = {
    "purpose_name", "court_designation", "disposal_name",
}


def normalized_change_value(field_name: str, value: Any) -> str:
    """Normalize only fields where formatting has no legal state meaning."""
    text = "" if value is None else str(value).strip()
    if field_name in _COSMETIC_TEXT_FIELDS:
        return re.sub(r"[^A-Z0-9]+", "", text.upper())
    return text


def is_material_backup_change(field_name: str, before: Any, after: Any) -> bool:
    """Return False for case/spacing/punctuation-only text differences."""
    return normalized_change_value(field_name, before) != normalized_change_value(
        field_name, after
    )


def inspect_backup_record(cino: str) -> dict[str, Any]:
    """Return a safe inventory of fields present in one imported app backup row."""
    ensure_ecourts_schema()
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(cino or "")).upper()
    if not re.fullmatch(r"[A-Z0-9]{16}", normalized):
        raise ValueError("Enter the exact 16-character CNR.")

    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT source_kind, display_case_number, raw_payload
            FROM ecourts_backup_records
            WHERE cino=%s
            """,
            (normalized,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(
                "CNR was not found in the latest imported backup. "
                "Run /syncecourts first."
            )
        payload = row[2] if isinstance(row[2], dict) else {}
        populated = sorted(
            str(key)
            for key, value in payload.items()
            if value not in (None, "", [], {})
        )
        order_tokens = (
            "order", "judgment", "interim", "document", "pdf", "download",
            "proceeding", "history", "hearing",
        )
        reference_tokens = ("url", "uri", "link", "path", "file", "id")
        return {
            "cino": normalized,
            "source_kind": row[0],
            "display_case_number": row[1],
            "field_count": len(payload),
            "populated_fields": populated,
            "order_fields": sorted(
                key for key in populated
                if any(token in key.lower() for token in order_tokens)
            ),
            "reference_fields": sorted(
                key for key in populated
                if any(token in key.lower() for token in reference_tokens)
            ),
        }
    finally:
        cur.close()
        conn.close()


def normalize_case_number(value: Any) -> str:
    text = str(value or "").upper().replace("CASE", "")
    parts = [part for part in re.split(r"[^A-Z0-9]+", text) if part]
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
        year = int(parts[-1])
        if year < 100:
            year += 2000
        return f"{parts[0]}{int(parts[-2])}{year}"
    return re.sub(r"[^A-Z0-9]", "", text)


def normalize_name(value: Any) -> str:
    text = re.sub(r"\b(VERSUS|VS\.?|AND|ORS?|DEF(?:ENDANT)?S?)\b", " ", str(value or "").upper())
    return re.sub(r"[^A-Z0-9]", "", text)


def _case_columns(cur) -> set[str]:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'cases'
    """)
    return {row[0] for row in cur.fetchall()}


def _local_cases(cur) -> list[dict[str, Any]]:
    columns = _case_columns(cur)
    preferred = [
        "id", "case_id", "case_number", "case_title", "client_name",
        "opposite_party", "court_name", "next_hearing", "hearing_date", "status",
        "ecourts_cnr",
    ]
    chosen = [name for name in preferred if name in columns]
    if not chosen:
        return []
    cur.execute("SELECT " + ", ".join(f'"{name}"' for name in chosen) + " FROM cases")
    rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(zip(chosen, row))
        pk = item.get("id") or item.get("case_id") or item.get("case_number")
        number = item.get("case_number") or item.get("case_id")
        if pk is None or not str(number or "").strip():
            continue
        item["_pk"] = str(pk)
        item["_number"] = str(number).strip()
        result.append(item)
    return result


def _party_score(local: dict[str, Any], backup: dict[str, Any]) -> float:
    local_title = normalize_name(local.get("case_title"))
    if not local_title:
        local_title = normalize_name(
            f"{local.get('client_name') or ''} {local.get('opposite_party') or ''}"
        )
    backup_title = normalize_name(
        f"{backup.get('petitioner_name') or ''} {backup.get('respondent_name') or ''}"
    )
    if not local_title or not backup_title:
        return 0.0
    return SequenceMatcher(None, local_title, backup_title).ratio()


def _case_parts(value: Any) -> tuple[str | None, int | None, int | None]:
    text = str(value or "").strip().upper()
    tokens = [item for item in re.split(r"[^A-Z0-9]+", text) if item]
    case_type = next((item for item in tokens if re.fullmatch(r"[A-Z()]+", item)), None)
    numbers = [int(item) for item in tokens if item.isdigit()]
    if not numbers:
        return case_type, None, None
    year = numbers[-1]
    if year < 100:
        year += 2000
    registration = numbers[-2] if len(numbers) >= 2 else None
    return case_type, registration, year


def _possible_assessment(
    local: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a conservative manual-match assessment, never an auto-link."""
    local_type, local_number, local_year = _case_parts(local.get("_number"))
    backup_type, backup_number, backup_year = _case_parts(
        backup.get("display_case_number")
    )
    party = _party_score(local, backup)
    type_match = bool(local_type and backup_type and local_type == backup_type)
    year_match = bool(local_year and backup_year and local_year == backup_year)
    number_match = bool(
        local_number is not None
        and backup_number is not None
        and local_number == backup_number
    )
    # Exact type/number/year is handled by the auto-link branch. A manual
    # suggestion must still agree on type and year and have strong party overlap.
    if not type_match or not year_match or party < 0.82:
        return None
    confidence = min(
        0.97,
        (party * 0.70) + (0.15 if type_match else 0)
        + (0.10 if year_match else 0) + (0.05 if number_match else 0),
    )
    return {
        "confidence": confidence,
        "match_strength": "STRONG" if party >= 0.92 else "VERIFY",
        "party_score": party,
        "type_match": type_match,
        "year_match": year_match,
        "number_match": number_match,
        "reasons": [
            f"Case type: {'match' if type_match else 'different'}",
            f"Registration year: {'match' if year_match else 'different'}",
            f"Registration number: {'match' if number_match else 'different'}",
            f"Party similarity: {party:.0%}",
        ],
    }


def _reconcile(
    cur,
    run_id: int,
    actor_id: int | None,
    apply_auto_links: bool = True,
) -> dict[str, Any]:
    locals_ = _local_cases(cur)
    cur.execute("""
        SELECT * FROM ecourts_backup_records
        WHERE last_sync_run_id = %s
    """, (run_id,))
    names = [column.name for column in cur.description]
    backups = [dict(zip(names, row)) for row in cur.fetchall()]
    by_number: dict[str, list[dict[str, Any]]] = {}
    for record in backups:
        by_number.setdefault(normalize_case_number(record["display_case_number"]), []).append(record)

    cur.execute("""
        SELECT l.local_case_pk, l.cino
        FROM ecourts_case_links l
        JOIN ecourts_backup_records b ON b.cino=l.cino
        WHERE l.link_status='APPROVED' AND b.last_sync_run_id=%s
    """, (run_id,))
    approved_local = {str(row[0]): row[1] for row in cur.fetchall()}
    approved_cino = set(approved_local.values())
    auto_links: list[tuple] = []
    matched_local: set[str] = set(approved_local)
    matched_cino: set[str] = set(approved_cino)
    possible: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    cur.execute("SELECT local_case_pk, cino FROM ecourts_match_rejections")
    rejected_pairs = {(str(row[0]), str(row[1])) for row in cur.fetchall()}
    provisional_possible: list[dict[str, Any]] = []

    for local in locals_:
        if local["_pk"] in matched_local:
            continue
        candidates = by_number.get(normalize_case_number(local["_number"]), [])
        if len(candidates) == 1 and candidates[0]["cino"] not in matched_cino:
            record = candidates[0]
            score = max(0.98, _party_score(local, record))
            auto_links.append((
                local["_pk"], local["_number"], record["cino"],
                "EXACT_CASE_NUMBER", score, actor_id,
            ))
            matched_local.add(local["_pk"])
            matched_cino.add(record["cino"])
        elif len(candidates) > 1:
            conflicts.append({"local": local, "reason": "Duplicate backup case number"})
        else:
            scored = sorted(
                (
                    (assessment, record)
                    for record in backups
                    if record["cino"] not in matched_cino
                    and (local["_pk"], record["cino"]) not in rejected_pairs
                    if (assessment := _possible_assessment(local, record)) is not None
                ),
                key=lambda pair: pair[0]["confidence"],
                reverse=True,
            )
            if scored:
                assessment, record = scored[0]
                provisional_possible.append({
                    "local": local,
                    "backup": record,
                    **assessment,
                })

    by_suggested_cino: dict[str, list[dict[str, Any]]] = {}
    for item in provisional_possible:
        by_suggested_cino.setdefault(item["backup"]["cino"], []).append(item)
    for cino, items in by_suggested_cino.items():
        if len(items) == 1:
            possible.append(items[0])
            continue
        for item in items:
            conflicts.append({
                "local": item["local"],
                "backup": item["backup"],
                "reason": (
                    f"Unsafe suggestion suppressed: CNR {cino} was the best "
                    f"candidate for {len(items)} Office OS cases"
                ),
            })

    if auto_links and apply_auto_links:
        execute_values(cur, """
            INSERT INTO ecourts_case_links
                (local_case_pk, local_case_number, cino, match_method,
                 confidence, link_status, approved_by)
            VALUES %s
            ON CONFLICT DO NOTHING
        """, [
            (pk, number, cino, method, confidence, "APPROVED", actor)
            for pk, number, cino, method, confidence, actor in auto_links
        ])
        for pk, _, cino, _, _, _ in auto_links:
            cur.execute("""
                UPDATE cases
                SET ecourts_cnr=%s, ecourts_last_synced_at=NOW()
                WHERE id::text=%s
            """, (cino, pk))
        execute_values(cur, """
            INSERT INTO ecourts_reconciliation_audit
                (action, local_case_pk, cino, details, actor_id)
            VALUES %s
        """, [
            ("AUTO_LINK", pk, cino, Json({"method": method, "confidence": confidence}), actor)
            for pk, _, cino, method, confidence, actor in auto_links
        ])

    cur.execute("""
        SELECT l.local_case_pk, l.cino
        FROM ecourts_case_links l
        JOIN ecourts_backup_records b ON b.cino=l.cino
        WHERE l.link_status='APPROVED' AND b.last_sync_run_id=%s
    """, (run_id,))
    all_links = cur.fetchall()
    linked_local = {str(row[0]) for row in all_links}
    linked_cino = {row[1] for row in all_links}
    office_only = [item for item in locals_ if item["_pk"] not in linked_local]
    backup_only = [item for item in backups if item["cino"] not in linked_cino]
    possible_local_ids = {
        str(item["local"]["_pk"])
        for item in possible
    }
    no_candidate = [
        item for item in office_only
        if str(item["_pk"]) not in possible_local_ids
    ]
    backup_only_active = [
        item for item in backup_only
        if not item.get("decision_date")
        and not item.get("disposal_name")
        and item.get("next_hearing_date")
    ]
    backup_only_disposed = [
        item for item in backup_only
        if item.get("decision_date") or item.get("disposal_name")
    ]
    backup_only_unknown = [
        item for item in backup_only
        if item not in backup_only_active and item not in backup_only_disposed
    ]
    return {
        "matched_count": len(all_links),
        "possible_count": len(possible),
        "office_only_count": len(office_only),
        "no_candidate_count": len(no_candidate),
        "backup_only_count": len(backup_only),
        "backup_only_active_count": len(backup_only_active),
        "backup_only_disposed_count": len(backup_only_disposed),
        "backup_only_unknown_count": len(backup_only_unknown),
        "conflict_count": len(conflicts),
        "office_only": office_only,
        "no_candidate": no_candidate,
        "backup_only": backup_only,
        "backup_only_active": backup_only_active,
        "backup_only_disposed": backup_only_disposed,
        "backup_only_unknown": backup_only_unknown,
        "possible": possible,
        "conflicts": conflicts,
    }


def synchronize_backups(actor_id: int | None = None) -> dict[str, Any]:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    run_id = None
    try:
        cur.execute(
            "INSERT INTO ecourts_backup_sync_runs (triggered_by) VALUES (%s) RETURNING id",
            (actor_id,),
        )
        run_id = cur.fetchone()[0]
        conn.commit()
        district = parse_backup(_download_text(DISTRICT_BACKUP_ID), "DISTRICT")
        high_court = parse_backup(_download_text(HIGH_COURT_BACKUP_ID), "HIGH_COURT")
        raw_records = district + high_court
        records, duplicate_count = deduplicate_backup_records(raw_records)
        if duplicate_count:
            logger.warning(
                "eCourts backup contained %s duplicate CNR row(s); "
                "freshest rows retained before upsert",
                duplicate_count,
            )
        cinos = [item["cino"] for item in records]
        existing: dict[str, dict[str, Any]] = {}
        if cinos:
            cur.execute("""
                SELECT cino, next_hearing_date, last_hearing_date,
                       purpose_name, court_designation, decision_date,
                       disposal_name, raw_payload
                FROM ecourts_backup_records
                WHERE cino = ANY(%s)
            """, (cinos,))
            for row in cur.fetchall():
                existing[row[0]] = {
                    "next_hearing_date": row[1],
                    "last_hearing_date": row[2],
                    "purpose_name": row[3],
                    "court_designation": row[4],
                    "decision_date": row[5],
                    "disposal_name": row[6],
                    "updated": (row[7] or {}).get("updated") if isinstance(row[7], dict) else None,
                }
        tracked = (
            "next_hearing_date", "last_hearing_date", "purpose_name",
            "court_designation", "decision_date", "disposal_name", "updated",
        )
        detected_changes: list[dict[str, Any]] = []
        for item in records:
            old = existing.get(item["cino"])
            if not old:
                continue
            current = dict(item)
            current["updated"] = item["raw_payload"].get("updated")
            for field in tracked:
                before = old.get(field)
                after = current.get(field)
                if not is_material_backup_change(field, before, after):
                    continue
                before_text = "" if before is None else str(before)
                after_text = "" if after is None else str(after)
                severity = "INFO"
                if field in {"decision_date", "disposal_name"} and after_text:
                    severity = "CRITICAL"
                elif field in {"next_hearing_date", "purpose_name", "court_designation"}:
                    severity = "IMPORTANT"
                detected_changes.append({
                    "cino": item["cino"],
                    "display_case_number": item["display_case_number"],
                    "field_name": field,
                    "old_value": before_text or None,
                    "new_value": after_text or None,
                    "severity": severity,
                })
        values = [(
            item["cino"], item["source_kind"], item["case_type"],
            item["registration_number"], item["registration_year"],
            item["display_case_number"], item["raw_case_number"],
            item["petitioner_name"], item["respondent_name"],
            item["establishment_name"], item["establishment_code"],
            item["state_name"], item["district_name"], item["court_designation"],
            item["last_hearing_date"], item["next_hearing_date"],
            item["decision_date"], item["purpose_name"], item["disposal_name"],
            item["note"], Json(item["raw_payload"]), run_id,
        ) for item in records]
        if values:
            execute_values(cur, """
                INSERT INTO ecourts_backup_records (
                    cino, source_kind, case_type, registration_number,
                    registration_year, display_case_number, raw_case_number,
                    petitioner_name, respondent_name, establishment_name,
                    establishment_code, state_name, district_name,
                    court_designation, last_hearing_date, next_hearing_date,
                    decision_date, purpose_name, disposal_name, note,
                    raw_payload, last_sync_run_id
                ) VALUES %s
                ON CONFLICT (cino) DO UPDATE SET
                    source_kind=EXCLUDED.source_kind,
                    case_type=EXCLUDED.case_type,
                    registration_number=EXCLUDED.registration_number,
                    registration_year=EXCLUDED.registration_year,
                    display_case_number=EXCLUDED.display_case_number,
                    raw_case_number=EXCLUDED.raw_case_number,
                    petitioner_name=EXCLUDED.petitioner_name,
                    respondent_name=EXCLUDED.respondent_name,
                    establishment_name=EXCLUDED.establishment_name,
                    establishment_code=EXCLUDED.establishment_code,
                    state_name=EXCLUDED.state_name,
                    district_name=EXCLUDED.district_name,
                    court_designation=EXCLUDED.court_designation,
                    last_hearing_date=EXCLUDED.last_hearing_date,
                    next_hearing_date=EXCLUDED.next_hearing_date,
                    decision_date=EXCLUDED.decision_date,
                    purpose_name=EXCLUDED.purpose_name,
                    disposal_name=EXCLUDED.disposal_name,
                    note=EXCLUDED.note,
                    raw_payload=EXCLUDED.raw_payload,
                    last_seen_at=NOW(),
                    last_sync_run_id=EXCLUDED.last_sync_run_id
            """, values)
        result = _reconcile(cur, run_id, actor_id, apply_auto_links=True)
        for change in detected_changes:
            cur.execute("""
                SELECT local_case_pk FROM ecourts_case_links
                WHERE cino=%s AND link_status='APPROVED'
            """, (change["cino"],))
            linked = cur.fetchone()
            cur.execute("""
                INSERT INTO ecourts_case_changes (
                    sync_run_id, cino, local_case_pk, display_case_number,
                    field_name, old_value, new_value, severity
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (sync_run_id, cino, field_name) DO NOTHING
            """, (
                run_id, change["cino"], linked[0] if linked else None,
                change["display_case_number"], change["field_name"],
                change["old_value"], change["new_value"], change["severity"],
            ))
        cur.execute("""
            UPDATE ecourts_backup_sync_runs SET
                finished_at=NOW(), status='SUCCESS',
                district_count=%s, high_court_count=%s,
                matched_count=%s, possible_count=%s,
                office_only_count=%s, backup_only_count=%s, conflict_count=%s
            WHERE id=%s
        """, (
            len(district), len(high_court), result["matched_count"],
            result["possible_count"], result["office_only_count"],
            result["backup_only_count"], result["conflict_count"], run_id,
        ))
        conn.commit()
        result.update({
            "run_id": run_id,
            "status": "SUCCESS",
            "district_count": len(district),
            "high_court_count": len(high_court),
            "total_backup_count": len(records),
            "raw_backup_count": len(raw_records),
            "duplicate_cnr_rows_ignored": duplicate_count,
            "change_count": len(detected_changes),
            "changes": detected_changes,
        })
        # Compare-only verification phase. Staff dates remain operational until
        # an administrator explicitly accepts a fresh eCourts conflict.
        try:
            from services.ecourts_date_verification_service import (
                reconcile_date_verifications,
            )
            result["date_verification"] = reconcile_date_verifications(run_id)
        except Exception as verification_exc:
            # Reconciliation backup import is already committed successfully.
            # A verification-layer defect must not relabel that import as failed.
            logger.exception("eCourts date verification phase failed safely")
            result["date_verification"] = {
                "status": "UNAVAILABLE",
                "error": (
                    f"{type(verification_exc).__name__}: {verification_exc}"
                )[:500],
            }
        return result
    except Exception as exc:
        conn.rollback()
        if run_id:
            cur.execute("""
                UPDATE ecourts_backup_sync_runs
                SET finished_at=NOW(), status='FAILED', error_message=%s
                WHERE id=%s
            """, (f"{type(exc).__name__}: {exc}"[:2000], run_id))
            conn.commit()
        raise
    finally:
        cur.close()
        conn.close()


def list_ecourts_changes(
    limit: int = 50,
    only_unalerted: bool = False,
    review_status: str | None = None,
) -> list[dict[str, Any]]:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if only_unalerted:
            clauses.append("ch.alerted_at IS NULL")
        if review_status:
            clauses.append("ch.review_status=%s")
            params.append(str(review_status).upper())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(f"""
            SELECT ch.id, ch.sync_run_id, ch.cino, ch.display_case_number, ch.field_name,
                   ch.old_value, ch.new_value, ch.severity, ch.detected_at,
                   ch.alerted_at, c.case_title, c.client_name,
                   ch.review_status, ch.reviewed_at, ch.reviewed_by,
                   ch.applied_at, ch.apply_message, ch.local_case_pk
            FROM ecourts_case_changes ch
            LEFT JOIN cases c ON c.id::text=ch.local_case_pk
            {where}
            ORDER BY ch.id DESC
            LIMIT %s
        """, (*params, max(1, min(int(limit), 200))))
        names = [item[0] for item in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def list_ecourts_change_groups(
    limit: int = 25,
    review_status: str = "PENDING",
    only_unalerted: bool = False,
) -> list[dict[str, Any]]:
    """Return one administrator-review item per case and synchronization run."""
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ch.sync_run_id, ch.cino,
                   COALESCE(ch.local_case_pk, link.local_case_pk) AS local_case_pk,
                   MAX(ch.display_case_number) AS display_case_number,
                   MAX(c.case_title) AS case_title,
                   MAX(c.client_name) AS client_name,
                   MAX(ch.detected_at) AS detected_at,
                   ARRAY_AGG(ch.id ORDER BY ch.id) AS change_ids,
                   JSONB_AGG(
                       JSONB_BUILD_OBJECT(
                           'id', ch.id,
                           'field_name', ch.field_name,
                           'old_value', ch.old_value,
                           'new_value', ch.new_value,
                           'severity', ch.severity
                       ) ORDER BY ch.id
                   ) AS changes,
                   CASE
                       WHEN BOOL_OR(ch.severity='CRITICAL') THEN 'CRITICAL'
                       WHEN BOOL_OR(ch.severity='IMPORTANT') THEN 'IMPORTANT'
                       ELSE 'INFO'
                   END AS severity
            FROM ecourts_case_changes ch
            LEFT JOIN ecourts_case_links link
              ON link.cino=ch.cino AND link.link_status='APPROVED'
            LEFT JOIN cases c
              ON c.id::text=COALESCE(ch.local_case_pk, link.local_case_pk)
            WHERE ch.review_status=%s
              AND (%s=FALSE OR ch.alerted_at IS NULL)
            GROUP BY ch.sync_run_id, ch.cino,
                     COALESCE(ch.local_case_pk, link.local_case_pk)
            ORDER BY MAX(ch.id) DESC
            LIMIT %s
        """, (
            str(review_status).upper(),
            bool(only_unalerted),
            max(1, min(int(limit), 100)),
        ))
        names = [item[0] for item in cur.description]
        groups = [dict(zip(names, row)) for row in cur.fetchall()]

        order_table_exists = False
        cur.execute("SELECT to_regclass('ecourts_order_inbox')")
        order_table_exists = bool(cur.fetchone()[0])
        for group in groups:
            group["order_status"] = "AWAITING_PDF"
            group["order_drive_link"] = None
            if not order_table_exists:
                continue
            cur.execute("""
                SELECT processing_status,
                       COALESCE(archived_drive_link, original_link)
                FROM ecourts_order_inbox
                WHERE cino=%s OR local_case_pk=%s
                ORDER BY id DESC
                LIMIT 1
            """, (group["cino"], group.get("local_case_pk")))
            order_row = cur.fetchone()
            if order_row:
                group["order_status"] = order_row[0] or "FOUND"
                group["order_drive_link"] = order_row[1]
        return groups
    finally:
        cur.close()
        conn.close()


def _case_columns(cur) -> set[str]:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='cases'
    """)
    return {str(row[0]) for row in cur.fetchall()}


def review_ecourts_change(
    change_id: int,
    decision: str,
    actor_id: int,
) -> dict[str, Any]:
    """Approve/reject one detected change. No change is applied without this call."""
    decision = str(decision or "").strip().upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("Decision must be APPROVE or REJECT.")
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, cino, local_case_pk, display_case_number, field_name,
                   old_value, new_value, severity, review_status
            FROM ecourts_case_changes
            WHERE id=%s
            FOR UPDATE
        """, (int(change_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("eCourts change was not found.")
        names = [column.name for column in cur.description]
        item = dict(zip(names, row))
        if item["review_status"] != "PENDING":
            raise ValueError(
                f"This change is already {str(item['review_status']).lower()}."
            )
        if decision == "REJECT":
            message = "Rejected by administrator; Office OS was not changed."
            cur.execute("""
                UPDATE ecourts_case_changes
                SET review_status='REJECTED', reviewed_at=NOW(),
                    reviewed_by=%s, apply_message=%s
                WHERE id=%s
            """, (int(actor_id), message, int(change_id)))
        else:
            local_pk = item.get("local_case_pk")
            if not local_pk:
                raise ValueError("This eCourts record is not linked to an Office OS case.")
            columns = _case_columns(cur)
            candidates = {
                "next_hearing_date": (),
                "purpose_name": ("next_purpose",),
                "disposal_name": ("status",),
            }.get(item["field_name"], ())
            target = next((name for name in candidates if name in columns), None)
            if not target:
                message = (
                    "Approved for record, but this field has no safe Office OS mapping; "
                    "no local value was changed."
                )
                status = "APPROVED_NO_MAPPING"
                applied_at = None
            else:
                # target is selected only from the fixed whitelist above.
                cur.execute(
                    f"UPDATE cases SET {target}=%s, ecourts_last_synced_at=NOW() "
                    "WHERE id::text=%s",
                    (item.get("new_value"), str(local_pk)),
                )
                if cur.rowcount != 1:
                    raise ValueError("Linked Office OS case could not be updated.")
                message = f"Applied to cases.{target} after administrator approval."
                status = "APPLIED"
                applied_at = "NOW()"
            cur.execute(f"""
                UPDATE ecourts_case_changes
                SET review_status=%s, reviewed_at=NOW(), reviewed_by=%s,
                    applied_at={applied_at or 'NULL'}, apply_message=%s
                WHERE id=%s
            """, (status, int(actor_id), message, int(change_id)))
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit (
                action, local_case_pk, cino, details, actor_id
            ) VALUES (%s,%s,%s,%s,%s)
        """, (
            f"CHANGE_{decision}",
            item.get("local_case_pk"),
            item.get("cino"),
            Json({
                "change_id": int(change_id),
                "field_name": item.get("field_name"),
                "old_value": item.get("old_value"),
                "new_value": item.get("new_value"),
                "result": message,
            }),
            int(actor_id),
        ))
        conn.commit()
        item.update({"review_status": status if decision == "APPROVE" else "REJECTED"})
        item["apply_message"] = message
        return item
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def review_ecourts_change_group(
    sync_run_id: int,
    cino: str,
    decision: str,
    actor_id: int,
) -> dict[str, Any]:
    """Review every pending field for one case/run in a single local transaction."""
    decision = str(decision or "").strip().upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("Decision must be APPROVE or REJECT.")
    normalized_cino = str(cino or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{16}", normalized_cino):
        raise ValueError("Invalid CNR.")
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, cino, local_case_pk, display_case_number, field_name,
                   old_value, new_value, severity, review_status
            FROM ecourts_case_changes
            WHERE sync_run_id=%s AND cino=%s AND review_status='PENDING'
            ORDER BY id
            FOR UPDATE
        """, (int(sync_run_id), normalized_cino))
        names = [column.name for column in cur.description]
        items = [dict(zip(names, row)) for row in cur.fetchall()]
        if not items:
            cur.execute("""
                SELECT display_case_number, local_case_pk,
                       ARRAY_AGG(id ORDER BY id),
                       ARRAY_AGG(DISTINCT review_status)
                FROM ecourts_case_changes
                WHERE sync_run_id=%s AND cino=%s
                GROUP BY display_case_number, local_case_pk
                ORDER BY MAX(id) DESC
                LIMIT 1
            """, (int(sync_run_id), normalized_cino))
            reviewed = cur.fetchone()
            if reviewed:
                statuses = [str(value) for value in (reviewed[3] or [])]
                return {
                    "sync_run_id": int(sync_run_id),
                    "cino": normalized_cino,
                    "local_case_pk": reviewed[1],
                    "display_case_number": reviewed[0],
                    "change_ids": list(reviewed[2] or []),
                    "review_status": "ALREADY_REVIEWED",
                    "applied_fields": [],
                    "unmapped_fields": [],
                    "apply_message": (
                        "This case update was already reviewed. "
                        f"Stored status: {', '.join(statuses) or 'REVIEWED'}. "
                        "No duplicate update was performed."
                    ),
                }
            raise ValueError("The referenced eCourts change group was not found.")
        local_pks = {
            str(item.get("local_case_pk"))
            for item in items
            if item.get("local_case_pk") is not None
            and str(item.get("local_case_pk")).strip()
        }
        if len(local_pks) > 1:
            raise ValueError(
                "The grouped changes contain conflicting Office OS case references."
            )
        stored_local_pk = next(iter(local_pks), None)
        cur.execute("""
            SELECT DISTINCT local_case_pk
            FROM ecourts_case_links
            WHERE cino=%s AND link_status='APPROVED'
        """, (normalized_cino,))
        approved_local_pks = {
            str(row[0]) for row in cur.fetchall() if row[0] is not None
        }
        if len(approved_local_pks) > 1:
            raise ValueError(
                "This CNR has conflicting approved Office OS case links."
            )
        approved_local_pk = next(iter(approved_local_pks), None)
        if (
            stored_local_pk
            and approved_local_pk
            and stored_local_pk != approved_local_pk
        ):
            raise ValueError(
                "The historical change reference conflicts with the approved CNR link."
            )
        local_pk = approved_local_pk or stored_local_pk
        if decision == "APPROVE" and not approved_local_pk:
            raise ValueError(
                "This CNR is not linked to an approved Office OS case. "
                "Link the case first, then review the update."
            )
        if local_pk:
            cur.execute("SELECT 1 FROM cases WHERE id::text=%s", (local_pk,))
            if not cur.fetchone():
                raise ValueError("The referenced Office OS case no longer exists.")
        change_ids = [int(item["id"]) for item in items]
        if local_pk:
            cur.execute("""
                UPDATE ecourts_case_changes
                SET local_case_pk=%s
                WHERE id=ANY(%s) AND local_case_pk IS NULL
            """, (local_pk, change_ids))

        if decision == "REJECT":
            message = (
                "All changes in this case update were rejected; "
                "Office OS was not changed."
            )
            cur.execute("""
                UPDATE ecourts_case_changes
                SET review_status='REJECTED', reviewed_at=NOW(),
                    reviewed_by=%s, apply_message=%s
                WHERE id=ANY(%s)
            """, (int(actor_id), message, change_ids))
            applied_fields: list[str] = []
            unmapped_fields: list[str] = []
            group_status = "REJECTED"
        else:
            columns = _case_columns(cur)
            mappings = {
                "next_hearing_date": (),
                "last_hearing_date": ("last_hearing_date", "last_hearing"),
                "purpose_name": ("next_purpose",),
                "disposal_name": ("status",),
                "court_designation": ("court_name", "court"),
            }
            applied_fields = []
            unmapped_fields = []
            for item in items:
                candidates = mappings.get(item["field_name"], ())
                target = next((name for name in candidates if name in columns), None)
                if not target:
                    unmapped_fields.append(str(item["field_name"]))
                    cur.execute("""
                        UPDATE ecourts_case_changes
                        SET review_status='APPROVED_NO_MAPPING',
                            reviewed_at=NOW(), reviewed_by=%s,
                            apply_message=%s
                        WHERE id=%s
                    """, (
                        int(actor_id),
                        "Approved for record; no safe Office OS column mapping exists.",
                        int(item["id"]),
                    ))
                    continue
                # The SQL identifier is selected only from the fixed whitelist above.
                cur.execute(
                    f"UPDATE cases SET {target}=%s, ecourts_last_synced_at=NOW() "
                    "WHERE id::text=%s",
                    (item.get("new_value"), local_pk),
                )
                if cur.rowcount != 1:
                    raise ValueError("Linked Office OS case could not be updated.")
                applied_fields.append(str(item["field_name"]))
                cur.execute("""
                    UPDATE ecourts_case_changes
                    SET review_status='APPLIED', reviewed_at=NOW(),
                        reviewed_by=%s, applied_at=NOW(), apply_message=%s
                    WHERE id=%s
                """, (
                    int(actor_id),
                    f"Applied safely to cases.{target} as part of grouped review.",
                    int(item["id"]),
                ))

            values = {str(item["field_name"]): item.get("new_value") for item in items}
            order_status = "AWAITING_PDF"
            cur.execute("SELECT to_regclass('ecourts_order_inbox')")
            if cur.fetchone()[0]:
                cur.execute("""
                    SELECT processing_status
                    FROM ecourts_order_inbox
                    WHERE cino=%s OR local_case_pk=%s
                    ORDER BY id DESC LIMIT 1
                """, (normalized_cino, local_pk))
                order_row = cur.fetchone()
                if order_row:
                    order_status = order_row[0] or "FOUND"
            cur.execute("""
                INSERT INTO ecourts_preparation_queue (
                    local_case_pk, cino, source_sync_run_id, source_change_ids,
                    next_hearing_date, purpose_name, order_status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (local_case_pk, cino, source_sync_run_id)
                DO UPDATE SET
                    source_change_ids=EXCLUDED.source_change_ids,
                    next_hearing_date=EXCLUDED.next_hearing_date,
                    purpose_name=EXCLUDED.purpose_name,
                    order_status=EXCLUDED.order_status,
                    queue_status='READY_FOR_REVIEW'
            """, (
                local_pk,
                normalized_cino,
                int(sync_run_id),
                change_ids,
                values.get("next_hearing_date"),
                values.get("purpose_name"),
                order_status,
            ))
            group_status = "APPLIED" if applied_fields else "APPROVED_NO_MAPPING"
            message = (
                f"Grouped review completed: {len(applied_fields)} field(s) applied, "
                f"{len(unmapped_fields)} retained for record only. "
                f"Preparation follow-up queued; order status: {order_status}."
            )

        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit (
                action, local_case_pk, cino, details, actor_id
            ) VALUES (%s,%s,%s,%s,%s)
        """, (
            f"CHANGE_GROUP_{decision}",
            local_pk,
            normalized_cino,
            Json({
                "sync_run_id": int(sync_run_id),
                "change_ids": change_ids,
                "applied_fields": applied_fields,
                "unmapped_fields": unmapped_fields,
                "result": message,
            }),
            int(actor_id),
        ))
        conn.commit()
        return {
            "sync_run_id": int(sync_run_id),
            "cino": normalized_cino,
            "local_case_pk": local_pk,
            "display_case_number": items[0].get("display_case_number"),
            "change_ids": change_ids,
            "review_status": group_status,
            "applied_fields": applied_fields,
            "unmapped_fields": unmapped_fields,
            "apply_message": message,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def ecourts_operations_summary() -> dict[str, Any]:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE review_status='PENDING'),
                COUNT(*) FILTER (
                    WHERE review_status='PENDING' AND severity='CRITICAL'
                ),
                COUNT(*) FILTER (
                    WHERE review_status='PENDING' AND severity='IMPORTANT'
                )
            FROM ecourts_case_changes
        """)
        pending, critical, important = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*)
            FROM cases
            WHERE COALESCE(status, 'OPEN') NOT IN ('CLOSED','DISPOSED')
              AND NULLIF(BTRIM(COALESCE(ecourts_cnr, '')), '') IS NULL
        """)
        missing_cnr = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*)
            FROM ecourts_case_links
            WHERE link_status='APPROVED'
        """)
        linked = cur.fetchone()[0]
        order_counts = {"unmatched": 0, "failed": 0, "important": 0}
        cur.execute("SELECT to_regclass('ecourts_order_inbox')")
        if cur.fetchone()[0]:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE processing_status='UNMATCHED'),
                    COUNT(*) FILTER (WHERE processing_status='FAILED'),
                    COUNT(*) FILTER (
                        WHERE importance IN ('IMPORTANT','CRITICAL')
                          AND processing_status<>'DUPLICATE'
                    )
                FROM ecourts_order_inbox
            """)
            unmatched, failed, important_orders = cur.fetchone()
            order_counts = {
                "unmatched": unmatched,
                "failed": failed,
                "important": important_orders,
            }
        cur.execute("""
            SELECT finished_at, status
            FROM ecourts_backup_sync_runs
            ORDER BY id DESC LIMIT 1
        """)
        latest = cur.fetchone()
        return {
            "pending_changes": pending,
            "critical_changes": critical,
            "important_changes": important,
            "missing_cnr": missing_cnr,
            "linked_cases": linked,
            "unmatched_orders": order_counts["unmatched"],
            "failed_orders": order_counts["failed"],
            "important_orders": order_counts["important"],
            "last_sync_at": latest[0] if latest else None,
            "last_sync_status": latest[1] if latest else "NOT_RUN",
        }
    finally:
        cur.close()
        conn.close()


def mark_ecourts_changes_alerted(change_ids: list[int]) -> None:
    if not change_ids:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE ecourts_case_changes SET alerted_at=NOW()
            WHERE id = ANY(%s)
        """, ([int(item) for item in change_ids],))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def latest_reconciliation() -> dict[str, Any]:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM ecourts_backup_sync_runs
            ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
        run = dict(zip((column.name for column in cur.description), row)) if row else None
        if not run:
            return {"status": "NOT_RUN"}
        result = dict(run)
        if run["status"] == "SUCCESS":
            details = _reconcile(cur, run["id"], None, apply_auto_links=False)
            result.update(details)
            conn.rollback()  # summary reads must not add new auto-links
        return result
    finally:
        cur.close()
        conn.close()


def approve_link(local_case_pk: str, cino: str, actor_id: int) -> None:
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        local = next((x for x in _local_cases(cur) if x["_pk"] == str(local_case_pk)), None)
        if not local:
            raise ValueError("Office case was not found.")
        cur.execute("SELECT cino FROM ecourts_backup_records WHERE cino=%s", (cino.upper(),))
        if not cur.fetchone():
            raise ValueError("CNR was not found in the imported backup.")
        cur.execute("""
            INSERT INTO ecourts_case_links
                (local_case_pk, local_case_number, cino, match_method,
                 confidence, link_status, approved_by)
            VALUES (%s,%s,%s,'ADMIN_APPROVED',1,'APPROVED',%s)
            ON CONFLICT (local_case_pk) DO UPDATE SET
                cino=EXCLUDED.cino, match_method='ADMIN_APPROVED',
                confidence=1, link_status='APPROVED',
                approved_by=EXCLUDED.approved_by, updated_at=NOW()
        """, (str(local_case_pk), local["_number"], cino.upper(), actor_id))
        cur.execute("""
            UPDATE cases
            SET ecourts_cnr=%s, ecourts_last_synced_at=NOW()
            WHERE id::text=%s
        """, (cino.upper(), str(local_case_pk)))
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit
                (action, local_case_pk, cino, details, actor_id)
            VALUES ('ADMIN_APPROVE',%s,%s,%s,%s)
        """, (str(local_case_pk), cino.upper(), Json({"case_number": local["_number"]}), actor_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def reject_match(
    local_case_pk: str,
    cino: str,
    actor_id: int,
    reason: str = "Not the same case",
) -> None:
    """Permanently suppress one unsafe local/CNR suggestion pair."""
    ensure_ecourts_schema()
    normalized_cino = re.sub(r"[^A-Za-z0-9]", "", str(cino or "")).upper()
    if not re.fullmatch(r"[A-Z0-9]{16}", normalized_cino):
        raise ValueError("Invalid CNR.")
    conn = _conn()
    cur = conn.cursor()
    try:
        local = next(
            (
                item for item in _local_cases(cur)
                if item["_pk"] == str(local_case_pk)
            ),
            None,
        )
        if not local:
            raise ValueError("Office OS case was not found.")
        cur.execute(
            "SELECT 1 FROM ecourts_backup_records WHERE cino=%s",
            (normalized_cino,),
        )
        if not cur.fetchone():
            raise ValueError("eCourts backup CNR was not found.")
        cur.execute("""
            INSERT INTO ecourts_match_rejections (
                local_case_pk, cino, rejected_by, reason
            ) VALUES (%s,%s,%s,%s)
            ON CONFLICT (local_case_pk, cino) DO UPDATE SET
                rejected_by=EXCLUDED.rejected_by,
                rejected_at=NOW(),
                reason=EXCLUDED.reason
        """, (
            str(local_case_pk), normalized_cino, int(actor_id), str(reason)[:500],
        ))
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit (
                action, local_case_pk, cino, details, actor_id
            ) VALUES ('MATCH_REJECT',%s,%s,%s,%s)
        """, (
            str(local_case_pk),
            normalized_cino,
            Json({"case_number": local["_number"], "reason": str(reason)[:500]}),
            int(actor_id),
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def create_reconciled_drive_export(actor_id: int) -> list[dict[str, Any]]:
    """Create dated district and High Court copies; never alter originals."""
    ensure_ecourts_schema()
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id FROM ecourts_backup_sync_runs
            WHERE status='SUCCESS' ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Run /syncecourts before creating an export.")
        latest_run_id = row[0]
        drive = get_drive_service()
        if drive is None:
            raise RuntimeError("Google Drive is not connected.")
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        created_files: list[dict[str, Any]] = []
        for source_kind, prefix in (
            ("DISTRICT", "myCases"),
            ("HIGH_COURT", "hcMyCases"),
        ):
            cur.execute("""
                SELECT raw_payload FROM ecourts_backup_records
                WHERE last_sync_run_id=%s AND source_kind=%s
                ORDER BY display_case_number, cino
            """, (latest_run_id, source_kind))
            payload = [
                json.dumps(item[0], ensure_ascii=False)
                for item in cur.fetchall()
            ]
            content = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            name = f"{prefix}-reconciled-{stamp}.txt"
            media = MediaIoBaseUpload(
                io.BytesIO(content), mimetype="text/plain", resumable=False
            )
            created_files.append(drive.files().create(
                body={"name": name, "parents": [ROOT_FOLDER_ID]},
                media_body=media,
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            ).execute())
        cur.execute("""
            INSERT INTO ecourts_reconciliation_audit
                (action, details, actor_id)
            VALUES ('EXPORT_CREATED',%s,%s)
        """, (Json(created_files), actor_id))
        conn.commit()
        return created_files
    finally:
        cur.close()
        conn.close()
