"""Owner authorization and office-wide replies for WhatsApp."""
import os
import sys
import types
import unittest
from unittest.mock import patch

psycopg2 = sys.modules.get("psycopg2") or types.ModuleType("psycopg2")
extras = sys.modules.get("psycopg2.extras") or types.ModuleType("psycopg2.extras")
extras.RealDictCursor = object
psycopg2.extras = extras
sys.modules["psycopg2"] = psycopg2
sys.modules["psycopg2.extras"] = extras

config = types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules.setdefault("config", config)

activity = types.ModuleType("services.staff_activity_service")
activity.ensure_staff_activity_schema = lambda: None
activity.record_staff_activity = lambda **kwargs: 1
sys.modules.setdefault("services.staff_activity_service", activity)

cloud = sys.modules.get("services.whatsapp_cloud") or types.ModuleType("services.whatsapp_cloud")
cloud.normalize_phone = lambda value: "".join(c for c in str(value) if c.isdigit())
cloud.send_text_message = lambda phone, text: {"provider_message_id": "wamid.test"}
sys.modules["services.whatsapp_cloud"] = cloud

from services.whatsapp_owner_companion import (  # noqa: E402
    _owner_attendance,
    _owner_overview,
    _owner_work,
    classify_owner_command,
    handle_owner_inbound,
    link_owner_phone,
    match_tagged_staff,
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
    def test_physical_file_commands_are_owner_actions(self):
        self.assertEqual(classify_owner_command("files"), ("FILES", ""))
        self.assertEqual(classify_owner_command("files review"), ("FILES_REVIEW", ""))
        self.assertEqual(classify_owner_command("files clear"), ("FILES_CLEAR", ""))
        self.assertEqual(classify_owner_command("files auto"), ("FILES_AUTO", ""))

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

    def test_owner_work_shows_case_title_and_number(self):
        cursor = Cursor(task_rows=[{
            "task_id": 56,
            "staff_name": "Preet",
            "task_text": "Correct address",
            "case_number": "CS/3712/2018",
            "case_title": "Asha Rani versus Mohan Lal",
            "deadline": "05-05-2025",
            "due_at": None,
        }])
        message, page, total_pages = _owner_work(cursor)
        self.assertEqual((page, total_pages), (1, 1))
        self.assertIn("Asha Rani versus Mohan Lal", message)
        self.assertIn("CS/3712/2018", message)
        self.assertNotIn("⚖️ CS/3712/2018", message)

    def test_owner_work_second_page_contains_remaining_items(self):
        rows = [{
            "task_id": number,
            "staff_name": "Happy",
            "task_text": f"Work {number}",
            "case_number": f"CS/{number}/2026",
            "case_title": f"Case Title {number}",
            "deadline": None,
            "due_at": f"2026-10-{number:02d}",
        } for number in range(1, 16)]
        cursor = Cursor(task_rows=rows)
        message, page, total_pages = _owner_work(cursor, 2)
        self.assertEqual((page, total_pages), (2, 2))
        self.assertIn("Showing 11-15 of 15", message)
        self.assertIn("Page 2 of 2", message)

    def test_owner_work_page_command(self):
        self.assertEqual(classify_owner_command("WORK 2"), ("WORK", "2"))

    def test_owner_attendance_command(self):
        self.assertEqual(
            classify_owner_command("TODAY ATTENDANCE"), ("ATTENDANCE", "")
        )

    def test_owner_attendance_lists_present_and_absent_staff(self):
        cursor = Cursor(task_rows=[
            {
                "staff_name": "Happy", "checkin_time": "09:30",
                "checkout_time": None, "status": "OPEN",
                "checkin_office_name": "Court Chamber Office",
                "checkout_office_name": None, "working_minutes": None,
            },
            {
                "staff_name": "Preet", "checkin_time": None,
                "checkout_time": None, "status": None,
                "checkin_office_name": None, "checkout_office_name": None,
                "working_minutes": None,
            },
        ])
        message = _owner_attendance(cursor)
        self.assertIn("Happy — Present", message)
        self.assertIn("Preet — Not checked in", message)
        self.assertIn("Present/attended: 1 of 2", message)

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

    def test_owner_messaging_commands_are_explicit(self):
        self.assertEqual(classify_owner_command("MESSAGE STAFF"), ("MESSAGE", ""))
        self.assertEqual(
            classify_owner_command("BROADCAST Meeting at 5 PM"),
            ("BROADCAST", "Meeting at 5 PM"),
        )
        self.assertEqual(classify_owner_command("cancel"), ("CANCEL", ""))

    def test_tagged_staff_uses_longest_exact_name(self):
        rows = [
            {"staff_name": "Preet", "whatsapp_phone": "911"},
            {"staff_name": "Preet Office", "whatsapp_phone": "922"},
        ]
        matched = match_tagged_staff("@Preet Office Please bring the file", rows)
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0]["whatsapp_phone"], "922")
        self.assertEqual(matched[1], "Please bring the file")

    def test_partial_tag_does_not_match(self):
        rows = [{"staff_name": "Happy", "whatsapp_phone": "933"}]
        self.assertIsNone(match_tagged_staff("@Happyness hello", rows))


if __name__ == "__main__":
    unittest.main()
