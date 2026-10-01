import sys
import types
import unittest


psycopg2 = types.ModuleType("psycopg2")
sys.modules.setdefault("psycopg2", psycopg2)

requests = types.ModuleType("requests")
sys.modules.setdefault("requests", requests)

config = types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules.setdefault("config", config)

drive = types.ModuleType("utils.drive")
drive.get_or_create_case_folder = lambda _case: ("folder", "link")
sys.modules.setdefault("utils.drive", drive)

from services.ad_sync_v3 import (  # noqa: E402
    mobile_sync_decision,
    parse_client_payload,
)


class AdvocateDiariesMobileSyncTests(unittest.TestCase):
    def test_primary_phone_is_normalized(self):
        parsed = parse_client_payload({
            "id": "ad-1", "name": "Client", "primary_phone": "98765 43210",
        })
        self.assertEqual(parsed["mobile"], "919876543210")

    def test_common_mobile_field_is_imported(self):
        parsed = parse_client_payload({
            "id": "ad-2", "name": "Client", "mobile_number": "+91-99888-77665",
        })
        self.assertEqual(parsed["mobile"], "919988877665")

    def test_nested_whatsapp_field_is_imported(self):
        parsed = parse_client_payload({
            "id": "ad-3", "name": "Client",
            "details": {"whatsapp_number": "98150 12345"},
        })
        self.assertEqual(parsed["mobile"], "919815012345")

    def test_blank_local_number_is_imported(self):
        self.assertEqual(
            mobile_sync_decision("", "", "9876543210"),
            ("IMPORTED", "919876543210"),
        )

    def test_identical_number_is_unchanged(self):
        self.assertEqual(
            mobile_sync_decision("919876543210", "", "+91 98765 43210"),
            ("UNCHANGED", "919876543210"),
        )

    def test_conflicting_manual_number_is_preserved(self):
        self.assertEqual(
            mobile_sync_decision("919876543210", "", "9988877665"),
            ("CONFLICT", "919876543210"),
        )


if __name__ == "__main__":
    unittest.main()
