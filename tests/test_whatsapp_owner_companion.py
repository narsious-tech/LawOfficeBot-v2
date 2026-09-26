"""Owner authorization and office-wide replies for WhatsApp."""
import os
import sys
import types
import unittest
from unittest.mock import patch

psycopg2 = types.ModuleType("psycopg2")
extras = types.ModuleType("psycopg2.extras")
extras.RealDictCursor = object
psycopg2.extras = extras
sys.modules.setdefault("psycopg2", psycopg2)
sys.modules.setdefault("psycopg2.extras", extras)

config = types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules.setdefault("config", config)

activity = types.ModuleType("services.staff_activity_service")
activity.ensure_staff_activity_schema = lambda: None
activity.record_staff_activity = lambda **kwargs: 1
sys.modules.setdefault("services.staff_activity_service", activity)

cloud = types.ModuleType("services.whatsapp_cloud")
cloud.normalize_phone = lambda value: "".join(c for c in str(value) if c.isdigit())
sys.modules.setdefault("services.whatsapp_cloud", cloud)

from services.whatsapp_owner_companion import (  # noqa: E402
    _owner_overview,
    classify_owner_command,
    handle_owner_inbound,
    link_owner_phone,
)


class Cursor:
    def __init__(self, *, authorized=False, task_rows=None):
        self.authorized = authorized
        self.task_rows = task_rows or []
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return {"?column?": 1} if self.authorized else None

    def fetchall(self):
        return self.task_rows


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **kwargs):
        return self._cursor

    def close(self):
        pass

    def rollback(self):
        pass


class OwnerTests(unittest.TestCase):
    def test_owner_number_is_matched_exactly_before_disclosing_data(self):
        cursor = Cursor(authorized=False)
        with patch.dict(os.environ, {
            "ADMIN_USER_ID": "12345", "WHATSAPP_STAFF_COMPANION_ENABLED": "true"
        }), patch("services.whatsapp_owner_companion.ensure_owner_schema"), patch.object(
            psycopg2, "connect", return_value=Connection(cursor), create=True
        ):
            result = handle_owner_inbound({"phone": "919999999999", "text": "WORK"})
        self.assertEqual(result, {"is_owner": False})
        self.assertEqual(len(cursor.queries), 1)
        self.assertEqual(cursor.queries[0][1], ("919999999999", 12345))

    def test_owner_can_see_office_counts_with_legacy_dates(self):
        rows = [
            {"task_id": 1, "staff_name": "Happy", "due_at": None,
             "deadline": "05-05-2025"},
            {"task_id": 2, "staff_name": "Priya", "due_at": "2999-01-01",
             "deadline": None},
            {"task_id": 3, "staff_name": "Happy", "due_at": None,
             "deadline": "Not fixed"},
        ]
        cursor = Cursor(authorized=True, task_rows=rows)
        with patch.dict(os.environ, {
            "ADMIN_USER_ID": "12345", "WHATSAPP_STAFF_COMPANION_ENABLED": "true"
        }), patch("services.whatsapp_owner_companion.ensure_owner_schema"), patch.object(
            psycopg2, "connect", return_value=Connection(cursor), create=True
        ):
            result = handle_owner_inbound({"phone": "919815908700", "text": "OVERVIEW"})
        self.assertTrue(result["is_owner"])
        self.assertIn("Pending work: 3", result["reply"])
        self.assertIn("Overdue: 1", result["reply"])
        self.assertIn("Happy: 2", result["reply"])
        self.assertIn("Priya: 1", result["reply"])

    def test_owner_link_requires_telegram_admin(self):
        with patch.dict(os.environ, {"ADMIN_USER_ID": "12345"}):
            with self.assertRaises(PermissionError):
                link_owner_phone("919815908700", 55555)

    def test_staff_number_cannot_be_promoted_to_owner(self):
        cursor = Cursor(authorized=True)
        cursor.fetchone = lambda: {"staff_name": "Happy"}
        with patch.dict(os.environ, {"ADMIN_USER_ID": "12345"}), patch(
            "services.whatsapp_owner_companion.ensure_owner_schema"
        ), patch.object(psycopg2, "connect", return_value=Connection(cursor), create=True):
            with self.assertRaisesRegex(ValueError, "already linked to staff"):
                link_owner_phone("919815908700", 12345)
        self.assertEqual(len(cursor.queries), 1)

    def test_owner_commands_do_not_expose_staff_menu(self):
        self.assertEqual(classify_owner_command("HI"), ("MENU", ""))
        self.assertEqual(classify_owner_command("Staff Activity"), ("ACTIVITY", ""))
        self.assertEqual(classify_owner_command("case CS/2112/2022"),
                         ("CASE", "CS/2112/2022"))


if __name__ == "__main__":
    unittest.main()
