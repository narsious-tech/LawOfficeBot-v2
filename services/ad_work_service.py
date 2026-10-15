"""Normalized Advocate Diaries work records for LawOfficeBot.

This module is read-only. It fetches and parses Advocate Diaries work lists,
deduplicates only by authoritative work ID, and returns stable records used by
both the work list and dashboard/task alignment layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
import re

from bs4 import BeautifulSoup


@dataclass(frozen=True, slots=True)
class AdvocateWork:
    work_id: str | None
    client: str
    case_title: str
    case_type: str
    case_number: str
    next_hearing: str
    description: str
    case_details: str

    @property
    def stable_key(self) -> str:
        if self.work_id:
            return f"id:{self.work_id}"
        return "fallback:" + "|".join(
            part.strip().casefold()
            for part in (
                self.client,
                self.case_title,
                self.case_number,
                self.description,
            )
        )


def _extract_work_id(row) -> str | None:
    link = row.find(
        "a",
        href=lambda href: href and "mark_as_complete" in href,
    )
    if not link:
        return None

    href = link.get("href", "")
    query = parse_qs(urlparse(href).query)
    values = query.get("work") or query.get("work_id") or query.get("id")
    if values and values[0]:
        return str(values[0]).strip()

    match = re.search(r"(?:work|work_id|id)=([^&]+)", href or "")
    return match.group(1).strip() if match else None


def parse_works_html(html: str) -> list[AdvocateWork]:
    soup = BeautifulSoup(html or "", "lxml")
    parsed: list[AdvocateWork] = []
    seen_ids: set[str] = set()

    # Advocate Diaries has used both a conventional table and responsive
    # Bootstrap rows for this screen.  Parse either representation.
    tbody = soup.find("tbody")
    rows = tbody.find_all("tr", recursive=False) if tbody else []
    if not rows:
        rows = [
            row for row in soup.select(".row")
            if "Case Title:" in row.get_text(" ", strip=True)
            and len(row.find_all(recursive=False)) >= 3
        ]

    for row in rows:
        cols = row.find_all("td", recursive=False)
        if len(cols) < 3:
            cols = row.find_all(recursive=False)
        if len(cols) < 3:
            continue

        client = cols[0].get_text(" ", strip=True)
        case_details = cols[1].get_text("\n", strip=True)
        description = cols[2].get_text(" ", strip=True)
        work_id = _extract_work_id(row)

        # Suppress only duplicate authoritative IDs. Identical text attached
        # to different IDs remains a separate valid Advocate Diaries work.
        if work_id:
            if work_id in seen_ids:
                continue
            seen_ids.add(work_id)

        case_title = ""
        case_type = ""
        case_number = ""
        next_hearing = ""
        lines = [
            line.strip()
            for line in case_details.split("\n")
            if line.strip()
        ]

        # Labels and values can be separated by nested spans, <br> or newlines.
        # Stop at the next known label so a blank field cannot consume it.
        label_pattern = r"Case\s+Title|Case\s+Type|Case\s+(?:Number|No\.?)|Next\s+Hearing"
        labelled = "\n".join(lines)
        values = {}
        for match in re.finditer(rf"({label_pattern})\s*:\s*(.*?)(?=(?:{label_pattern})\s*:|$)", labelled, re.I | re.S):
            label = re.sub(r"\s+", " ", match.group(1)).lower().rstrip('.')
            values[label] = re.sub(r"\s+", " ", match.group(2)).strip()
        case_title = values.get("case title", "")
        case_type = values.get("case type", "")
        case_number = values.get("case number", "") or values.get("case no", "")
        next_hearing = values.get("next hearing", "")

        # Current AD layout: title, then "Civil SUIT · CS/5365/2025",
        # followed by a hearing label/date and a status line (without Case labels).
        if not case_number:
            for index, line in enumerate(lines):
                match = re.fullmatch(r"([^:]+?)\s*[·•|]\s*(.+\d.*)", line)
                if not match:
                    continue
                candidate_type, candidate_number = match.groups()
                # Do not treat labelled fields or later dates/status as a case row.
                if index != 1 or any(re.match(r"(?:Case|Next)\s", value, re.I) for value in (lines[0], candidate_type)):
                    continue
                case_type = case_type or candidate_type.strip()
                case_number = candidate_number.strip()
                case_title = case_title or lines[0].strip()
                break
        if next_hearing:
            # Status belongs to the work, not the hearing date.
            next_hearing = re.sub(r"\s+(?:pending|completed|complete|closed|done)\s*$", "", next_hearing, flags=re.I).strip()

        parsed.append(
            AdvocateWork(
                work_id=work_id,
                client=client,
                case_title=case_title,
                case_type=case_type,
                case_number=case_number,
                next_hearing=next_hearing,
                description=description,
                case_details=case_details,
            )
        )

    return parsed


def fetch_works(web, status: str = "pending") -> tuple[list[AdvocateWork], int]:
    """Return normalized records and HTTP status without performing writes."""
    response = web.works(status)
    if response.status_code != 200:
        return [], int(response.status_code)
    return parse_works_html(response.text), int(response.status_code)
