"""Reusable Telegram keyboards for LawOfficeBot navigation."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

from navigation.menu import MAIN_MENU_ROWS


def build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Build the persistent two-column office main menu."""
    rows = [
        [KeyboardButton(item.label) for item in row]
        for row in MAIN_MENU_ROWS
    ]

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Select an office module",
    )
