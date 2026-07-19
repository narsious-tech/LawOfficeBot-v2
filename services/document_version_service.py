"""Document naming and version allocation helpers."""

import os
import re
from typing import Optional

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or "Document"))[0]
    stem = _SAFE.sub("_", stem).strip("._-")
    return stem[:90] or "Document"


def build_versioned_filename(original_filename: str, version_label: str, sequence: Optional[int] = None) -> str:
    stem = sanitize_stem(original_filename)
    extension = os.path.splitext(original_filename or "")[1].lower() or ".bin"
    label = _SAFE.sub("_", version_label or "Final").strip("_") or "Final"
    suffix = f"_v{sequence}" if sequence and sequence > 1 else ""
    return f"{stem}_{label}{suffix}{extension}"


def next_version_sequence(connection, case_id: str, original_filename: str, version_label: str) -> int:
    """Return 1 for first upload, then 2, 3... for the same logical document/version."""
    stem = sanitize_stem(original_filename)
    label = _SAFE.sub("_", version_label or "Final").strip("_") or "Final"
    prefix = f"{stem}_{label}"
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT file_name FROM case_files
            WHERE LOWER(TRIM(case_id)) = LOWER(TRIM(%s))
              AND LOWER(file_name) LIKE LOWER(%s)
            """,
            (case_id, prefix + "%"),
        )
        names = [row[0] for row in cur.fetchall()]
    if not names:
        return 1
    highest = 1
    for name in names:
        match = re.search(r"_v(\d+)(?:\.[^.]+)?$", name or "", re.I)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1
