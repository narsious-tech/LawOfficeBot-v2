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
    classify_staff_command,
    staff_companion_enabled,
)


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


if __name__ == "__main__":
    unittest.main()
