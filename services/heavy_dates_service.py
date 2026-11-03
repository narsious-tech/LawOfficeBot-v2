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


def _purpose_key(value: Any) -> str:
    text = _clean(value, "Purpose not recorded").casefold()
    text = re.sub(r"^[\W_]*(?:for\s+)?", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip() or "purpose not recorded"


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
    by_date: dict[date, list[dict[str, str]]] = defaultdict(list)

    for raw in rows:
        status = _clean(_row_value(raw, "status", "case_status")).upper()
        if status in CLOSED_STATUSES:
            continue
        hearing_date = parse_hearing_date(
            _row_value(raw, "next_hearing", "hearing_date", "next_hearing_date")
        )
        if not hearing_date or hearing_date < start_date or hearing_date > end_date:
            continue
        case = {
            "case_number": _clean(_row_value(raw, "case_number", "case_id"), "Number not recorded"),
            "case_title": _clean(_row_value(raw, "case_title", "title"), "Title not recorded"),
            "purpose": _clean(_row_value(raw, "next_purpose", "purpose"), "Purpose not recorded"),
        }
        case["purpose_key"] = _purpose_key(case["purpose"])
        by_date[hearing_date].append(case)

    result: list[dict[str, Any]] = []
    for hearing_date, cases in by_date.items():
        purpose_counts = Counter(case["purpose_key"] for case in cases)
        largest_cluster = max(purpose_counts.values(), default=0)
        if len(cases) < minimum_matters and largest_cluster < minimum_same_purpose:
            continue

        purpose_labels: dict[str, Counter[str]] = defaultdict(Counter)
        purpose_cases: dict[str, list[dict[str, str]]] = defaultdict(list)
        for case in cases:
            purpose_labels[case["purpose_key"]][case["purpose"]] += 1
            purpose_cases[case["purpose_key"]].append(case)

        clusters = []
        for key, count in purpose_counts.most_common():
            label = purpose_labels[key].most_common(1)[0][0]
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
            "severity": severity,
            "purpose_clusters": clusters,
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
                f"{icon} {item['date'].strftime('%d %b')} · {item['total']} matters · "
                f"largest same-purpose group {item['largest_purpose_cluster']}"
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
        lines.extend([
            f"{icon} {item['date'].strftime('%A, %d %B %Y')}",
            f"⚖️ {item['total']} matters",
        ])
        repeated = [cluster for cluster in item["purpose_clusters"] if cluster["count"] >= 2]
        for cluster in repeated[:5]:
            numbers = ", ".join(case["case_number"] for case in cluster["cases"][:6])
            if len(cluster["cases"]) > 6:
                numbers += f", +{len(cluster['cases']) - 6} more"
            lines.append(f"• {cluster['purpose']} — {cluster['count']} ({numbers})")
        unclustered = item["total"] - sum(cluster["count"] for cluster in repeated)
        if unclustered:
            lines.append(f"• Other purposes — {unclustered}")
        lines.append("")
    if limit and len(items) > limit:
        lines.append(f"…and {len(items) - limit} more heavy date(s).")
    lines.append("Plan drafting, file preparation and staff allocation before these dates.")
    return "\n".join(lines).strip()

