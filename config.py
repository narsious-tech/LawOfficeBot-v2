"""
Central configuration for LawOfficeBot-v2.

The module reads deployment settings from environment variables and validates
the minimum configuration required to start the Telegram bot.

Existing modules may continue importing DATABASE_URL directly:

    from config import DATABASE_URL

New modules should prefer:

    from config import settings
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


def _read_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return a stripped environment-variable value or the supplied default."""
    value = os.getenv(name)

    if value is None:
        return default

    value = value.strip()
    return value if value else default


def _read_int(
    name: str,
    default: Optional[int] = None,
) -> Optional[int]:
    """Read an integer environment variable with clear validation errors."""
    value = _read_env(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be an integer, but received {value!r}."
        ) from exc


def _read_bool(name: str, default: bool = False) -> bool:
    """Read a conventional boolean environment variable."""
    value = _read_env(name)

    if value is None:
        return default

    normalized = value.lower()

    if normalized in {"1", "true", "yes", "y", "on"}:
        return True

    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise ConfigurationError(
        f"{name} must be true/false, yes/no, on/off, or 1/0."
    )


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings loaded from the environment."""

    # Core application
    bot_token: Optional[str]
    database_url: Optional[str]
    environment: str
    timezone: str
    log_level: str
    port: int
    debug: bool

    # Telegram / office routing
    office_group_chat_id: Optional[int]
    attendance_app_url: Optional[str]

    # Advocate Diaries
    ad_api: Optional[str]
    ad_email: Optional[str]
    ad_password: Optional[str]

    # Office profile
    office_name: str
    office_whatsapp_number: Optional[str]
    office_phone_number: Optional[str]
    office_email: Optional[str]
    court_office_address: str
    evening_office_address: Optional[str]
    office_hours: str
    office_website: Optional[str]
    court_office_maps_link: Optional[str]
    evening_office_maps_link: Optional[str]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def advocate_diaries_configured(self) -> bool:
        return bool(self.ad_api and self.ad_email and self.ad_password)

    def validate_core(self) -> None:
        """Validate settings required for normal Telegram-bot startup."""
        missing: list[str] = []

        if not self.bot_token:
            missing.append("BOT_TOKEN")

        if not self.database_url:
            missing.append("DATABASE_URL")

        if missing:
            raise ConfigurationError(
                "Missing required environment variables: "
                + ", ".join(missing)
            )

    def validate_advocate_diaries(self) -> None:
        """Validate settings required by Advocate Diaries operations."""
        missing: list[str] = []

        if not self.ad_api:
            missing.append("AD_API")

        if not self.ad_email:
            missing.append("AD_EMAIL")

        if not self.ad_password:
            missing.append("AD_PASSWORD")

        if missing:
            raise ConfigurationError(
                "Advocate Diaries is not fully configured. Missing: "
                + ", ".join(missing)
            )


def load_settings() -> Settings:
    """Load all supported settings from environment variables."""
    return Settings(
        bot_token=_read_env("BOT_TOKEN"),
        database_url=_read_env("DATABASE_URL"),
        environment=_read_env("APP_ENV", "development") or "development",
        timezone=_read_env("APP_TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata",
        log_level=(_read_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        port=_read_int("PORT", 8080) or 8080,
        debug=_read_bool("DEBUG", False),
        office_group_chat_id=_read_int("OFFICE_GROUP_CHAT_ID"),
        attendance_app_url=_read_env("ATTENDANCE_APP_URL"),
        ad_api=_read_env("AD_API"),
        ad_email=_read_env("AD_EMAIL"),
        ad_password=_read_env("AD_PASSWORD"),
        office_name=(
            _read_env("OFFICE_NAME", "Law Office of Ajay Chawla")
            or "Law Office of Ajay Chawla"
        ),
        office_whatsapp_number=_read_env("OFFICE_WHATSAPP_NUMBER"),
        office_phone_number=_read_env("OFFICE_PHONE_NUMBER"),
        office_email=_read_env("OFFICE_EMAIL"),
        court_office_address=(
            _read_env(
                "COURT_OFFICE_ADDRESS",
                "Chamber No. 247, District Courts, Ludhiana",
            )
            or "Chamber No. 247, District Courts, Ludhiana"
        ),
        evening_office_address=_read_env("EVENING_OFFICE_ADDRESS"),
        office_hours=(
            _read_env(
                "OFFICE_HOURS",
                "Monday-Saturday, 9:30 AM-6:30 PM",
            )
            or "Monday-Saturday, 9:30 AM-6:30 PM"
        ),
        office_website=_read_env("OFFICE_WEBSITE"),
        court_office_maps_link=_read_env("COURT_OFFICE_MAPS_LINK"),
        evening_office_maps_link=_read_env("EVENING_OFFICE_MAPS_LINK"),
    )


settings = load_settings()

# ---------------------------------------------------------------------------
# Backward-compatible exports
# ---------------------------------------------------------------------------
# Existing production modules can continue using the old uppercase names.
BOT_TOKEN = settings.bot_token
DATABASE_URL = settings.database_url

AD_API = settings.ad_api
AD_EMAIL = settings.ad_email
AD_PASSWORD = settings.ad_password

OFFICE_GROUP_CHAT_ID = settings.office_group_chat_id
ATTENDANCE_APP_URL = settings.attendance_app_url

OFFICE_NAME = settings.office_name
OFFICE_WHATSAPP_NUMBER = settings.office_whatsapp_number
OFFICE_PHONE_NUMBER = settings.office_phone_number
OFFICE_EMAIL = settings.office_email
COURT_OFFICE_ADDRESS = settings.court_office_address
EVENING_OFFICE_ADDRESS = settings.evening_office_address
OFFICE_HOURS = settings.office_hours
OFFICE_WEBSITE = settings.office_website
COURT_OFFICE_MAPS_LINK = settings.court_office_maps_link
EVENING_OFFICE_MAPS_LINK = settings.evening_office_maps_link

APP_ENV = settings.environment
APP_TIMEZONE = settings.timezone
LOG_LEVEL = settings.log_level
PORT = settings.port
DEBUG = settings.debug
