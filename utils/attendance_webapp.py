"""Resolve the public attendance Web App URL without stale Railway links."""

import os
from urllib.parse import urlsplit, urlunsplit


ATTENDANCE_PATH = "/attendance-app"


def _normalise_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"

    parts = urlsplit(value)
    path = (parts.path or "").rstrip("/")
    if not path or path == "/":
        path = ATTENDANCE_PATH
    elif not path.endswith(ATTENDANCE_PATH):
        path = f"{path}{ATTENDANCE_PATH}"

    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def get_attendance_app_url() -> str:
    """Prefer Railway's automatically maintained public domain."""
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    configured_url = os.getenv("ATTENDANCE_APP_URL", "")
    return _normalise_url(railway_domain or configured_url)


def get_attendance_url_source() -> str:
    if os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip():
        return "Railway managed domain"
    if os.getenv("ATTENDANCE_APP_URL", "").strip():
        return "ATTENDANCE_APP_URL fallback"
    return "not configured"