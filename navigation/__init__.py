"""Navigation framework for LawOfficeBot v3."""

from .keyboard import build_main_menu_keyboard
from .menu import MenuItem, get_main_menu_items
from .permissions import UserRole, resolve_user_role

__all__ = [
    "MenuItem",
    "UserRole",
    "build_main_menu_keyboard",
    "get_main_menu_items",
    "resolve_user_role",
]
