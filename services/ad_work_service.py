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
    tbody = soup.find("tbody")
    if tbody is None:
        return []

    parsed: list[AdvocateWork] = []
    seen_ids: set[str] = set()

    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
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

        for index, line in enumerate(lines):
            if line.startswith("Case Title:"):
                case_title = line.partition(":")[2].strip()
            elif line.startswith("Case Type:"):
                case_type = line.partition(":")[2].strip()
            elif line.startswith("Case Number:"):
                case_number = line.partition(":")[2].strip()
            elif line.startswith("Next Hearing:"):
                inline = line.partition(":")[2].strip()
                next_hearing = inline or (
                    lines[index + 1] if index + 1 < len(lines) else ""
                )

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
