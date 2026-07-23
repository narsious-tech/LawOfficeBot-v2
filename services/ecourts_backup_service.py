"""Read-only eCourts app backup import and Office OS reconciliation."""
from __future__ import annotations

import io
import json
import os
import re
from difflib import SequenceMatcher
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_values
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from config import DATABASE_URL
from utils.drive import get_drive_service, ROOT_FOLDER_ID

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
            CREATE INDEX IF NOT EXISTS idx_ecourts_backup_case_number
            ON ecourts_backup_records(display_case_number)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecourts_backup_last_run
            ON ecourts_backup_records(last_sync_run_id)
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
                    (_party_score(local, record), record)
                    for record in backups
                    if record["cino"] not in matched_cino
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )
            if scored and scored[0][0] >= 0.72:
                possible.append({
                    "local": local,
                    "backup": scored[0][1],
                    "confidence": scored[0][0],
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
    return {
        "matched_count": len(all_links),
        "possible_count": len(possible),
        "office_only_count": len(office_only),
        "backup_only_count": len(backup_only),
        "conflict_count": len(conflicts),
        "office_only": office_only,
        "backup_only": backup_only,
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
        records = district + high_court
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
        })
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
