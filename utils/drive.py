import os
import re
from typing import Optional, Tuple

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")


def extract_folder_id(value: str) -> str:
    """
    Accept either a raw Google Drive folder ID or a full folder URL
    and return only the folder ID.
    """
    value = (value or "").strip()

    match = re.search(
        r"/folders/([A-Za-z0-9_-]+)",
        value
    )

    if match:
        return match.group(1)

    return value


ROOT_FOLDER_ID = extract_folder_id(
    os.getenv(
        "ROOT_FOLDER_ID",
        "1WIeKgxvHFylvQ8Um1l48TkqZGvxr65mS"
    )
)

print(
    f"Google Drive Root Folder: {ROOT_FOLDER_ID}"
)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

_drive_service = None
_drive_error: Optional[str] = None


def build_drive_service(
    force_rebuild: bool = False
):
    """
    Build and cache one Google Drive API service for the whole application.

    Returns:
        Google Drive service object, or None when authentication is unavailable.
    """
    global _drive_service, _drive_error

    if (
        _drive_service is not None
        and not force_rebuild
    ):
        return _drive_service

    _drive_service = None
    _drive_error = None

    missing = []

    if not GOOGLE_CLIENT_ID:
        missing.append(
            "GOOGLE_CLIENT_ID"
        )

    if not GOOGLE_CLIENT_SECRET:
        missing.append(
            "GOOGLE_CLIENT_SECRET"
        )

    if not GOOGLE_REFRESH_TOKEN:
        missing.append(
            "GOOGLE_REFRESH_TOKEN"
        )

    if missing:
        _drive_error = (
            "Missing Google OAuth variable(s): "
            + ", ".join(missing)
        )

        print(
            "Google Drive disabled: "
            f"{_drive_error}"
        )

        return None

    credentials = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri=(
            "https://oauth2.googleapis.com/token"
        ),
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=[DRIVE_SCOPE]
    )

    try:
        credentials.refresh(
            Request()
        )

        _drive_service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False
        )

        print(
            "Google Drive connected."
        )

        return _drive_service

    except RefreshError as exc:
        _drive_error = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "Google Drive disabled: "
            f"{_drive_error}"
        )

        return None

    except Exception as exc:
        _drive_error = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "Google Drive setup failed: "
            f"{_drive_error}"
        )

        return None


def get_drive_service():
    """
    Return the cached Drive service, building it when first requested.
    """
    return build_drive_service(
        force_rebuild=False
    )


def reconnect_drive_service():
    """
    Force a fresh Drive service build using the current environment variables.
    """
    return build_drive_service(
        force_rebuild=True
    )


def drive_is_connected() -> bool:
    return (
        get_drive_service()
        is not None
    )


def get_drive_error() -> Optional[str]:
    return _drive_error


def ensure_root_folder_id():
    """
    Confirm that the configured Google Drive root folder ID is available.
    """
    if not ROOT_FOLDER_ID:
        raise RuntimeError(
            "ROOT_FOLDER_ID is empty."
        )


def escape_drive_query_value(
    value: str
) -> str:
    """
    Escape a string before inserting it into a Google Drive query.
    """
    return (
        value
        .replace("\\", "\\\\")
        .replace("'", "\\'")
    )


def normalize_folder_name(
    case_value: str
) -> str:
    value = (
        case_value
        or ""
    ).strip()

    if not value:
        raise ValueError(
            "Case number or folder name is required."
        )

    return value.replace(
        "/",
        "-"
    )


def find_case_folder(
    case_value: str
) -> Optional[
    Tuple[str, Optional[str]]
]:
    """
    Find an existing case folder inside ROOT_FOLDER_ID.

    Returns:
        (folder_id, web_view_link), or None.
    """
    ensure_root_folder_id()

    service = get_drive_service()

    if service is None:
        raise RuntimeError(
            "Google Drive is currently disconnected."
        )

    safe_name = normalize_folder_name(
        case_value
    )

    escaped_name = escape_drive_query_value(
        safe_name
    )

    query = (
        f"name = '{escaped_name}' "
        "and mimeType = "
        "'application/vnd.google-apps.folder' "
        f"and '{ROOT_FOLDER_ID}' in parents "
        "and trashed = false"
    )

    result = (
        service
        .files()
        .list(
            q=query,
            fields=(
                "files(id, name, webViewLink)"
            ),
            spaces="drive",
            pageSize=10
        )
        .execute()
    )

    folders = result.get(
        "files",
        []
    )

    if not folders:
        return None

    folder = folders[0]

    return (
        folder["id"],
        folder.get(
            "webViewLink"
        )
    )


def create_case_folder(
    case_value: str
) -> Tuple[str, Optional[str]]:
    """
    Create a new case folder inside ROOT_FOLDER_ID.
    """
    ensure_root_folder_id()

    service = get_drive_service()

    if service is None:
        raise RuntimeError(
            "Google Drive is currently disconnected."
        )

    safe_name = normalize_folder_name(
        case_value
    )

    folder_metadata = {
        "name": safe_name,
        "mimeType": (
            "application/vnd.google-apps.folder"
        ),
        "parents": [
            ROOT_FOLDER_ID
        ]
    }

    folder = (
        service
        .files()
        .create(
            body=folder_metadata,
            fields=(
                "id, webViewLink"
            )
        )
        .execute()
    )

    return (
        folder["id"],
        folder.get(
            "webViewLink"
        )
    )


def get_or_create_case_folder(
    case_value: str
) -> Tuple[str, Optional[str]]:
    """
    Reuse an existing case folder or create it when missing.
    """
    existing = find_case_folder(
        case_value
    )

    if existing:
        return existing

    return create_case_folder(
        case_value
    )


def get_or_create_subfolder(
    parent_folder_id: str,
    folder_name: str
) -> Tuple[str, Optional[str]]:
    """
    Find or create a subfolder inside a case folder.
    """
    if not parent_folder_id:
        raise ValueError(
            "Parent folder ID is required."
        )

    service = get_drive_service()

    if service is None:
        raise RuntimeError(
            "Google Drive is currently disconnected."
        )

    clean_name = (
        folder_name
        or "Miscellaneous"
    ).strip()

    escaped_name = escape_drive_query_value(
        clean_name
    )

    escaped_parent_id = (
        escape_drive_query_value(
            parent_folder_id
        )
    )

    query = (
        f"name = '{escaped_name}' "
        "and mimeType = "
        "'application/vnd.google-apps.folder' "
        f"and '{escaped_parent_id}' in parents "
        "and trashed = false"
    )

    result = (
        service
        .files()
        .list(
            q=query,
            fields=(
                "files(id, name, webViewLink)"
            ),
            spaces="drive",
            pageSize=10
        )
        .execute()
    )

    folders = result.get(
        "files",
        []
    )

    if folders:
        folder = folders[0]

        return (
            folder["id"],
            folder.get(
                "webViewLink"
            )
        )

    metadata = {
        "name": clean_name,
        "mimeType": (
            "application/vnd.google-apps.folder"
        ),
        "parents": [
            parent_folder_id
        ]
    }

    folder = (
        service
        .files()
        .create(
            body=metadata,
            fields=(
                "id, webViewLink"
            )
        )
        .execute()
    )

    return (
        folder["id"],
        folder.get(
            "webViewLink"
        )
    )


# Build once when the module is imported.
# Failure does not crash the bot; get_drive_service() will return None.
drive_service = build_drive_service()
