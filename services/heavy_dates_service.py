"""Forecast hearing dates that may overload the office."""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from config import DATABASE_URL


CLOSED_STATUSES = {
    "CLOSED", "DISPOSED", "DECIDED", "ARCHIVED", "INACTIVE", "CANCELLED",
}
DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y",
)


def _positive_int(name: str, default: int, *, minimum: int = 1, maximum: int = 365) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def heavy_date_settings() -> dict[str, int]:
    return {
        "lookahead_days": _positive_int("HEAVY_DATE_LOOKAHEAD_DAYS", 45),
        "minimum_matters": _positive_int("HEAVY_DATE_MIN_CASES", 8),
        "minimum_same_purpose": _positive_int("HEAVY_PURPOSE_MIN_CASES", 3),
        "very_heavy_matters": _positive_int("VERY_HEAVY_DATE_MIN_CASES", 12),
        "very_heavy_purpose": _positive_int("VERY_HEAVY_PURPOSE_MIN_CASES", 6),
    }


def parse_hearing_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Advocate Diaries and Office OS occasionally store a time after the date.
    candidates = (text, text[:10])
    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _clean(value: Any, fallback: str = "") -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip() or fallback


def _purpose_keys(value: Any) -> list[str]:
    text = _clean(value).casefold()
    if not text:
        return []
    text = re.sub(r"^[\W_]*(?:for\s+)?", "", text)
    key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()
    groups: list[str] = []

    def add(group: str) -> None:
        if group not in groups:
            groups.append(group)

    if re.search(r"\b(?:arg|args|argument|arguments)\b", key):
        add("arguments")
    if re.search(r"\b(?:cons|consd|consideration)\b", key):
        add("consideration")
    if re.search(r"\b(?:pre|preliminary)\s+(?:evd|evidence)\b", key):
        add("preliminary evidence")
    elif re.search(r"\b(?:pro|prosecution)\s+(?:evd|evidence)\b", key):
        add("prosecution evidence")
    elif re.search(r"\b(?:p\s+(?:evd|evidence)|pws|plaintiff\s+(?:evd|evidence|witness))\b", key):
        add("plaintiff evidence")
    if re.search(r"\b(?:d\s+(?:evd|evidence)|dws|defendant\s+(?:evd|evidence|witness))\b", key):
        add("defendant evidence")
    if re.search(r"\breply\b", key):
        add("reply")
    if re.search(r"\bnotice\b", key):
        add("notice")
    if re.search(r"\bappearance\b", key):
        add("appearance")
    if re.search(r"\bcws\b", key):
        add("cws")
    if re.search(r"\bwdn\b", key):
        add("wdn")
    return groups or ([key] if key else [])


def _purpose_label(key: str, labels: Counter[str]) -> str:
    preferred = {
        "arguments": "Arguments",
        "consideration": "Consideration",
        "preliminary evidence": "Preliminary evidence",
        "plaintiff evidence": "Plaintiff evidence",
        "prosecution evidence": "Prosecution evidence",
        "defendant evidence": "Defendant evidence",
        "reply": "Reply",
        "notice": "Notice",
        "appearance": "Appearance",
        "cws": "CWS",
        "wdn": "WDN",
    }
    return preferred.get(key) or labels.most_common(1)[0][0]


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _number_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", value.casefold()) if part]


def _numbers_are_aliases(left: str, right: str) -> bool:
    a, b = _number_parts(left), _number_parts(right)
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and any(part.isalpha() for part in short) and long[-len(short):] == short


def _row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return None


def analyse_heavy_dates(
    rows: Iterable[dict[str, Any]],
    *,
    start_date: date,
    lookahead_days: int,
    minimum_matters: int,
    minimum_same_purpose: int,
    very_heavy_matters: int,
    very_heavy_purpose: int,
) -> list[dict[str, Any]]:
    end_date = start_date + timedelta(days=lookahead_days)
    by_date: dict[date, dict[str, dict[str, str]]] = defaultdict(dict)

    for raw in rows:
        status = _clean(_row_value(raw, "status", "case_status")).upper()
        if status in CLOSED_STATUSES:
            continue
        hearing_date = parse_hearing_date(
            _row_value(raw, "next_hearing", "hearing_date", "next_hearing_date")
        )
        if not hearing_date or hearing_date < start_date or hearing_date > end_date:
            continue
        number = _clean(_row_value(raw, "case_number", "case_id"), "Number not recorded")
        raw_purpose = _clean(_row_value(raw, "next_purpose", "purpose"))
        case = {
            "case_number": number,
            "case_title": _clean(_row_value(raw, "case_title", "title"), "Title not recorded"),
            "purpose": raw_purpose or "Purpose not recorded",
        }
        case["purpose_keys"] = _purpose_keys(raw_purpose)
        identity = re.sub(r"\s+", "", number).casefold()
        if identity == "numbernotrecorded":
            identity = "title:" + case["case_title"].casefold()
        existing_key = identity if identity in by_date[hearing_date] else None
        if existing_key is None:
            title_key = _title_key(case["case_title"])
            if title_key and title_key != "titlenotrecorded":
                for candidate_key, candidate in by_date[hearing_date].items():
                    if (
                        _title_key(candidate["case_title"]) == title_key
                        and _numbers_are_aliases(candidate["case_number"], number)
                    ):
                        existing_key = candidate_key
                        break
        existing = by_date[hearing_date].get(existing_key) if existing_key else None
        if not existing:
            by_date[hearing_date][identity] = case
        else:
            # Prefer the richer copy when Office OS contains duplicate case rows.
            if not existing["purpose_keys"] and case["purpose_keys"]:
                existing["purpose"] = case["purpose"]
                existing["purpose_keys"] = case["purpose_keys"]
            if existing["case_title"] == "Title not recorded" and case["case_title"] != "Title not recorded":
                existing["case_title"] = case["case_title"]
            if len(_number_parts(case["case_number"])) > len(_number_parts(existing["case_number"])):
                existing["case_number"] = case["case_number"]

    result: list[dict[str, Any]] = []
    for hearing_date, case_map in by_date.items():
        cases = list(case_map.values())
        purpose_counts = Counter(
            key for case in cases for key in case["purpose_keys"]
        )
        missing_purpose = sum(not case["purpose_keys"] for case in cases)
        largest_cluster = max(purpose_counts.values(), default=0)
        if len(cases) < minimum_matters and largest_cluster < minimum_same_purpose:
            continue

        purpose_labels: dict[str, Counter[str]] = defaultdict(Counter)
        purpose_cases: dict[str, list[dict[str, str]]] = defaultdict(list)
        for case in cases:
            for key in case["purpose_keys"]:
                purpose_labels[key][case["purpose"]] += 1
                purpose_cases[key].append(case)

        clusters = []
        for key, count in purpose_counts.most_common():
            label = _purpose_label(key, purpose_labels[key])
            clusters.append({
                "purpose": label,
                "count": count,
                "cases": sorted(purpose_cases[key], key=lambda item: item["case_number"].casefold()),
            })

        severity = "VERY_HEAVY" if (
            len(cases) >= very_heavy_matters or largest_cluster >= very_heavy_purpose
        ) else "HEAVY"
        result.append({
            "date": hearing_date,
            "total": len(cases),
            "largest_purpose_cluster": largest_cluster,
            "missing_purpose": missing_purpose,
            "severity": severity,
            "purpose_clusters": clusters,
            "cases": sorted(cases, key=lambda item: item["case_number"].casefold()),
        })

    return sorted(result, key=lambda item: (item["date"], -item["total"]))


def get_heavy_dates(*, lookahead_days: int | None = None, start_date: date | None = None) -> list[dict[str, Any]]:
    import psycopg2

    settings = heavy_date_settings()
    if lookahead_days is not None:
        settings["lookahead_days"] = max(1, min(365, int(lookahead_days)))
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            # to_jsonb keeps this compatible with older Office OS schemas.
            cur.execute("SELECT to_jsonb(c) FROM cases c")
            rows = [row[0] or {} for row in cur.fetchall()]
    finally:
        conn.close()
    if start_date is None:
        from zoneinfo import ZoneInfo

        start_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    return analyse_heavy_dates(rows, start_date=start_date, **settings)


def render_heavy_dates(
    items: list[dict[str, Any]],
    *,
    lookahead_days: int,
    compact: bool = False,
    limit: int | None = None,
) -> str:
    settings = heavy_date_settings()
    visible = items[:limit] if limit else items
    if compact:
        if not visible:
            return "📊 Heavy dates: None in the forecast window."
        lines = ["📊 UPCOMING HEAVY DATES"]
        for item in visible:
            icon = "🔴" if item["severity"] == "VERY_HEAVY" else "🟠"
            lines.append(
                f"{icon} {item['date'].strftime('%d %b')} · {item['total']} matters"
            )
        return "\n".join(lines)

    lines = [
        "📊 HEAVY HEARING DATES",
        f"📅 Forecast: next {lookahead_days} days",
        (
            f"⚙️ Warning: {settings['minimum_matters']}+ matters on one date "
            f"or {settings['minimum_same_purpose']}+ for the same purpose"
        ),
        "",
    ]
    if not visible:
        lines.append("✅ No heavy hearing date is presently detected.")
        return "\n".join(lines)

    for item in visible:
        icon = "🔴" if item["severity"] == "VERY_HEAVY" else "🟠"
        repeated = [cluster for cluster in item["purpose_clusters"] if cluster["count"] >= 2]
        cluster_text = ", ".join(
            f"{cluster['purpose']} {cluster['count']}" for cluster in repeated[:3]
        )
        suffix = f" · {cluster_text}" if cluster_text else ""
        missing = item.get("missing_purpose", 0)
        missing_text = f" · ⚠️ {missing} purpose missing" if missing else ""
        lines.append(
            f"{icon} {item['date'].strftime('%a, %d %b')} — {item['total']} matters"
            f"{suffix}{missing_text}"
        )
    if limit and len(items) > limit:
        lines.append(f"…and {len(items) - limit} more heavy date(s).")
    lines.extend([
        "",
        "Details: /heavydate DD-MM-YYYY",
        "Plan drafting, file preparation and staff allocation before these dates.",
    ])
    return "\n".join(lines).strip()


def render_heavy_date_detail(item: dict[str, Any] | None) -> str:
    if not item:
        return "❌ No heavy-date entry was found for that date."
    icon = "🔴" if item["severity"] == "VERY_HEAVY" else "🟠"
    lines = [
        f"{icon} HEARING LOAD — {item['date'].strftime('%A, %d %B %Y')}",
        f"⚖️ Unique matters: {item['total']}",
        "",
    ]
    repeated = [cluster for cluster in item["purpose_clusters"] if cluster["count"] >= 2]
    if repeated:
        lines.append("🎯 REPEATED PURPOSES")
        for cluster in repeated:
            lines.append(f"• {cluster['purpose']} — {cluster['count']}")
        lines.append("")
    missing = item.get("missing_purpose", 0)
    if missing:
        lines.extend([f"⚠️ Purpose missing in {missing} matter(s)", ""])
    lines.append("📋 CASES")
    for index, case in enumerate(item.get("cases") or [], 1):
        lines.extend([
            f"{index}. {case['case_number']} — {case['purpose']}",
            f"   {case['case_title']}",
        ])
    return "\n".join(lines)

