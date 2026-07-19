"""Google Drive folder helpers for the structured case document repository."""

from typing import Dict
from utils.drive import get_or_create_subfolder
from utils.document_categories import DOCUMENT_CATEGORIES

STANDARD_EXTRA_FOLDERS = ("Drafts", "Archive")


def ensure_case_document_folders(case_folder_id: str) -> Dict[str, str]:
    """Create missing standard subfolders and return their IDs."""
    if not case_folder_id:
        raise ValueError("Case Google Drive folder ID is required.")
    result: Dict[str, str] = {}
    for key, name in DOCUMENT_CATEGORIES.items():
        folder_id, _ = get_or_create_subfolder(case_folder_id, name)
        result[key] = folder_id
    for name in STANDARD_EXTRA_FOLDERS:
        folder_id, _ = get_or_create_subfolder(case_folder_id, name)
        result[name.upper()] = folder_id
    return result
