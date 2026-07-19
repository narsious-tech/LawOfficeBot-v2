"""Small, non-invasive role helper for navigation visibility.

Sprint 1A deliberately does not alter the existing staff or attendance schema.
The helper therefore resolves the administrator from environment variables and
uses a safe staff default for everyone else. A later sprint can replace this
resolver with the authoritative linked-staff role stored in PostgreSQL.
"""

from __future__ import annotations

import os
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    ADVOCATE = "advocate"
    CLERK = "clerk"
    OFFICE_MANAGER = "office_manager"
    STAFF = "staff"


def _configured_admin_ids() -> set[str]:
    values = {
        os.getenv("ADMIN_USER_ID", ""),
        os.getenv("ADMIN_CHAT_ID", ""),
    }
    return {value.strip() for value in values if value and value.strip()}


def resolve_user_role(telegram_user_id: int | str | None) -> UserRole:
    """Resolve a navigation role without changing current database logic."""
    if telegram_user_id is not None and str(telegram_user_id) in _configured_admin_ids():
        return UserRole.ADMIN
    return UserRole.STAFF


def can_access_route(role: UserRole, route: str) -> bool:
    """Return whether a role may open a Sprint 1A route.

    Existing commands continue to enforce their own production permissions.
    This layer only blocks clearly administrative placeholders.
    """
    admin_only = {"settings"}
    return route not in admin_only or role == UserRole.ADMIN
