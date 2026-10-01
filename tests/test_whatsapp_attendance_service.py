"""Focused safeguards for WhatsApp attendance location handling."""
import sys
import time
import types
import unittest
from datetime import datetime
from unittest.mock import patch

psycopg2 = sys.modules.get("psycopg2") or types.ModuleType("psycopg2")
extras = sys.modules.get("psycopg2.extras") or types.ModuleType("psycopg2.extras")
extras.RealDictCursor = object
psycopg2.extras = extras
sys.modules["psycopg2"] = psycopg2
sys.modules["psycopg2.extras"] = extras

config = sys.modules.get("config") or types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules["config"] = config

from services.whatsapp_attendance_service import (  # noqa: E402
    _distance_meters,
    _fresh_timestamp,
    _nearest_office,
    build_attendance_notification,
    review_attendance_location,
)


class WhatsAppAttendanceTests(unittest.TestCase):
    def test_court_only_scope_filters_the_office_query(self):
        class Cursor:
            params = None

            def execute(self, _query, params=()):
                self.params = params

            def fetchall(self):
                return [{
                    "id": 1, "office_name": "Court Chamber Office",
                    "latitude": 30.8999, "longitude": 75.8346,
                    "allowed_radius_meters": 300,
                }]

        cur = Cursor()
        office = _nearest_office(
            cur, 30.8999, 75.8346, "CHECKIN", "COURT_ONLY"
        )
        self.assertEqual(cur.params, ("Court Chamber Office",))
        self.assertEqual(office["office_name"], "Court Chamber Office")

    def test_same_coordinates_have_zero_distance(self):
        self.assertLess(_distance_meters(30.8999606, 75.8346954, 30.8999606, 75.8346954), 0.1)

    def test_old_location_timestamp_is_rejected(self):
        self.assertTrue(_fresh_timestamp(int(time.time())))
        self.assertFalse(_fresh_timestamp(int(time.time()) - 3600))

    def test_forwarded_location_is_rejected_before_database_write(self):
        with patch(
            "services.whatsapp_attendance_service.ensure_whatsapp_attendance_schema"
        ):
            result = review_attendance_location(
                "919999999999",
                {"telegram_user_id": 1, "staff_name": "Preet"},
                latitude=30.8999,
                longitude=75.8346,
                message_timestamp=int(time.time()),
                forwarded=True,
            )
        self.assertIn("Forwarded locations", result["reply"])

    def test_group_checkin_notification_contains_location_and_source(self):
        message = build_attendance_notification(
            staff_name="Samar Sharma",
            action="CHECKIN",
            office_name="Court Chamber Office",
            distance_meters=10.4,
            event_time=datetime(2026, 9, 29, 10, 34),
            map_link="https://www.google.com/maps?q=30.9,75.83",
        )
        self.assertIn("🟢 STAFF CHECK-IN", message)
        self.assertIn("Staff: Samar Sharma", message)
        self.assertIn("Source: WhatsApp", message)
        self.assertIn("Distance from office: 10 metres", message)
        self.assertIn("29-09-2026 10:34 AM", message)

    def test_group_checkout_notification_contains_working_time(self):
        message = build_attendance_notification(
            staff_name="Samar Sharma",
            action="CHECKOUT",
            office_name="Court Chamber Office",
            distance_meters=4,
            event_time=datetime(2026, 9, 29, 18, 5),
            map_link="https://www.google.com/maps?q=30.9,75.83",
            working_minutes=451,
        )
        self.assertIn("🔴 STAFF CHECK-OUT", message)
        self.assertIn("Working time: 7h 31m", message)


if __name__ == "__main__":
    unittest.main()
