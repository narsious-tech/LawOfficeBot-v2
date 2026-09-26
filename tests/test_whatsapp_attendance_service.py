"""Focused safeguards for WhatsApp attendance location handling."""
import sys
import time
import types
import unittest
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
    review_attendance_location,
)


class WhatsAppAttendanceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
