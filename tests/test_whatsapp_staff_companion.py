import os
import sys
import types
import unittest


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

from services.whatsapp_staff_companion import (  # noqa: E402
    _case_lookup,
    _my_work,
    _office_status,
    classify_staff_command,
    staff_companion_enabled,
)


class FakeCursor:
    def __init__(self, fetchone_rows=None, fetchall_rows=None):
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])

    def execute(self, _query, _params=None):
        return None

    def fetchone(self):
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self):
        return self.fetchall_rows.pop(0) if self.fetchall_rows else []


class WhatsAppStaffCompanionTests(unittest.TestCase):
    def test_buttons_and_text_map_to_staff_actions(self):
        self.assertEqual(classify_staff_command("My Work"), ("MY_WORK", ""))
        self.assertEqual(
            classify_staff_command("  office   status "), ("OFFICE_STATUS", "")
        )
        self.assertEqual(
            classify_staff_command("CASE CS/3848/2025"),
            ("CASE", "CS/3848/2025"),
        )
        self.assertEqual(classify_staff_command("check in"), ("ATTENDANCE", ""))
        self.assertEqual(classify_staff_command("unknown"), ("MENU", ""))

    def test_feature_flag_is_off_by_default(self):
        previous = os.environ.pop("WHATSAPP_STAFF_COMPANION_ENABLED", None)
        try:
            self.assertFalse(staff_companion_enabled())
            os.environ["WHATSAPP_STAFF_COMPANION_ENABLED"] = "true"
            self.assertTrue(staff_companion_enabled())
        finally:
            if previous is None:
                os.environ.pop("WHATSAPP_STAFF_COMPANION_ENABLED", None)
            else:
                os.environ["WHATSAPP_STAFF_COMPANION_ENABLED"] = previous

    def test_case_lookup_formats_real_dict_rows_as_values(self):
        cur = FakeCursor(fetchall_rows=[[
            {
                "case_number": "CS/3848/2025",
                "case_title": "Ajay Bajaj vs Bittu Bhatia",
                "next_hearing": "2026-10-10",
            }
        ]])
        message = _case_lookup(cur, "Ajay Bajaj")
        self.assertIn("Ajay Bajaj vs Bittu Bhatia", message)
        self.assertIn("CS/3848/2025", message)
        self.assertIn("2026-10-10", message)
        self.assertNotIn("⚖️ case_title", message)

    def test_my_work_formats_real_dict_rows_as_values(self):
        cur = FakeCursor(
            fetchone_rows=[{"task_table": "tasks"}],
            fetchall_rows=[[
                {
                    "task_id": 7,
                    "task_text": "Draft reply",
                    "task_case_number": "CS/3848/2025",
                    "deadline": None,
                    "due_at": "2026-10-01",
                    "case_title": "Ajay Bajaj vs Bittu Bhatia",
                }
            ]],
        )
        message = _my_work(cur, "Preet")
        self.assertIn("#7 · Ajay Bajaj vs Bittu Bhatia", message)
        self.assertIn("Draft reply", message)
        self.assertIn("2026-10-01", message)

    def test_office_status_reads_named_counts_and_attendance(self):
        cur = FakeCursor(fetchone_rows=[
            {"pending_count": 3, "overdue_count": 1},
            {"attendance_table": "attendance_sessions"},
            {"checkin_time": "09:30", "checkout_time": None},
        ])
        message = _office_status(
            cur, {"telegram_user_id": 123, "staff_name": "Preet"}
        )
        self.assertIn("Pending work: 3", message)
        self.assertIn("Overdue: 1", message)
        self.assertIn("Present / checked in", message)


if __name__ == "__main__":
    unittest.main()
