"""Focused tests for automatic WhatsApp morning delivery helpers."""
import os
import sys
import types
import unittest
from datetime import date

psycopg2 = sys.modules.get("psycopg2") or types.ModuleType("psycopg2")
extras = sys.modules.get("psycopg2.extras") or types.ModuleType("psycopg2.extras")
extras.RealDictCursor = object
psycopg2.extras = extras
sys.modules["psycopg2"] = psycopg2
sys.modules["psycopg2.extras"] = extras

config = sys.modules.get("config") or types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules["config"] = config

from services.whatsapp_morning_delivery_service import (  # noqa: E402
    _today_hearing_summary,
    automatic_morning_enabled,
    configured_morning_time,
    split_whatsapp_message,
)


class WhatsAppMorningDeliveryTests(unittest.TestCase):
    def test_automatic_delivery_is_off_by_default(self):
        old = os.environ.pop("WHATSAPP_AUTOMATIC_MORNING_ENABLED", None)
        try:
            self.assertFalse(automatic_morning_enabled())
            os.environ["WHATSAPP_AUTOMATIC_MORNING_ENABLED"] = "true"
            self.assertTrue(automatic_morning_enabled())
        finally:
            if old is None:
                os.environ.pop("WHATSAPP_AUTOMATIC_MORNING_ENABLED", None)
            else:
                os.environ["WHATSAPP_AUTOMATIC_MORNING_ENABLED"] = old

    def test_default_delivery_time_is_1005_ist(self):
        old_hour = os.environ.pop("WHATSAPP_MORNING_HOUR", None)
        old_minute = os.environ.pop("WHATSAPP_MORNING_MINUTE", None)
        try:
            value = configured_morning_time()
            self.assertEqual((value.hour, value.minute), (10, 5))
            self.assertEqual(str(value.tzinfo), "Asia/Kolkata")
        finally:
            if old_hour is not None:
                os.environ["WHATSAPP_MORNING_HOUR"] = old_hour
            if old_minute is not None:
                os.environ["WHATSAPP_MORNING_MINUTE"] = old_minute

    def test_message_split_preserves_content(self):
        message = ("First paragraph.\n\n" + "A" * 3900 + "\n\nLast paragraph.")
        chunks = split_whatsapp_message(message, 1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), message.replace("\n", ""))

    def test_hearing_summary_is_compact(self):
        result = {
            "date": date(2026, 9, 29), "source": "API", "total": 2,
            "groups": [{
                "court_name": "District Court", "judge_name": "Court 1",
                "floor": "1", "room": "101",
                "cases": [
                    {"case_title": "A vs B", "case_number": "CS/1/2026", "stage": "Evidence"},
                    {"case_title": "C vs D", "case_number": "CS/2/2026", "stage": "Reply"},
                ],
            }],
        }
        message = _today_hearing_summary(result)
        self.assertIn("Total matters: 2", message)
        self.assertIn("A vs B", message)
        self.assertIn("Floor 1", message)


if __name__ == "__main__":
    unittest.main()
