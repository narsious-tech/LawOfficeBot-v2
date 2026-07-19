"""UI helpers for the menu-driven professional document upload wizard."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from utils.document_categories import DOCUMENT_CATEGORIES, DOCUMENT_VERSIONS


def category_keyboard(case_db_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 Pleadings", callback_data=f"docupload:category:PLEADINGS:{case_db_id}"),
         InlineKeyboardButton("⚖️ Orders", callback_data=f"docupload:category:ORDERS:{case_db_id}")],
        [InlineKeyboardButton("🧾 Evidence", callback_data=f"docupload:category:EVIDENCE:{case_db_id}"),
         InlineKeyboardButton("📚 Judgments", callback_data=f"docupload:category:JUDGMENTS:{case_db_id}")],
        [InlineKeyboardButton("✉️ Correspondence", callback_data=f"docupload:category:CORRESPONDENCE:{case_db_id}"),
         InlineKeyboardButton("📎 Miscellaneous", callback_data=f"docupload:category:MISCELLANEOUS:{case_db_id}")],
        [InlineKeyboardButton("⬅️ Cancel", callback_data=f"casews:documents:{case_db_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def version_keyboard(case_db_id: int, category: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📝 Draft", callback_data=f"docupload:version:DRAFT:{category}:{case_db_id}"),
         InlineKeyboardButton("✅ Final", callback_data=f"docupload:version:FINAL:{category}:{case_db_id}")],
        [InlineKeyboardButton("🔄 Revised", callback_data=f"docupload:version:REVISED:{category}:{case_db_id}"),
         InlineKeyboardButton("✍️ Signed", callback_data=f"docupload:version:SIGNED:{category}:{case_db_id}")],
        [InlineKeyboardButton("🏛 Certified Copy", callback_data=f"docupload:version:CERTIFIED_COPY:{category}:{case_db_id}")],
        [InlineKeyboardButton("⬅️ Categories", callback_data=f"docupload:choose:{case_db_id}")],
    ]
    return InlineKeyboardMarkup(rows)
