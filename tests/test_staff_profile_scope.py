"""Staff role and attendance-office restriction safeguards."""
import sys
import types
import unittest

telegram = types.ModuleType("telegram")
for name in (
    "Update", "KeyboardButton", "ReplyKeyboardMarkup", "ReplyKeyboardRemove",
    "InlineKeyboardButton", "InlineKeyboardMarkup", "WebAppInfo",
):
    setattr(telegram, name, type(name, (), {}))
telegram_ext = types.ModuleType("telegram.ext")
telegram_ext.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})
telegram_ext.MessageHandler = type("MessageHandler", (), {})
telegram_ext.filters = object()
sys.modules.setdefault("telegram", telegram)
sys.modules.setdefault("telegram.ext", telegram_ext)

bs4 = types.ModuleType("bs4")
bs4.BeautifulSoup = object
sys.modules.setdefault("bs4", bs4)

advocate_web = types.ModuleType("advocate_web")
advocate_web.AdvocateWeb = type("AdvocateWeb", (), {})
sys.modules.setdefault("advocate_web", advocate_web)

psycopg2 = sys.modules.get("psycopg2") or types.ModuleType("psycopg2")
sys.modules.setdefault("psycopg2", psycopg2)
config = sys.modules.get("config") or types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules.setdefault("config", config)

attendance_webapp = types.ModuleType("utils.attendance_webapp")
attendance_webapp.get_attendance_app_url = lambda: ""
sys.modules.setdefault("utils.attendance_webapp", attendance_webapp)

from commands.attendance import (  # noqa: E402
    normalize_attendance_scope,
    normalize_staff_role,
)


class StaffProfileScopeTests(unittest.TestCase):
    def test_junior_associate_role_is_normalized(self):
        self.assertEqual(normalize_staff_role("Junior Associate"), "junior_associate")

    def test_court_only_scope_is_normalized(self):
        self.assertEqual(normalize_attendance_scope("court only"), "COURT_ONLY")

    def test_invalid_role_and_scope_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_staff_role("administrator")
        with self.assertRaises(ValueError):
            normalize_attendance_scope("home")


if __name__ == "__main__":
    unittest.main()
