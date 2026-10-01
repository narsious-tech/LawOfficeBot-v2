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

cloud = types.ModuleType("services.whatsapp_cloud")
cloud.normalize_phone = lambda value: "".join(c for c in str(value) if c.isdigit())
sys.modules.setdefault("services.whatsapp_cloud", cloud)

from services.whatsapp_client_reception import (  # noqa: E402
    _case_rows,
    _case_summary,
    _contact_message,
    _location_message,
    _selected_registered_case,
    classify_client_command,
    client_reception_enabled,
    reception_menu,
    reception_menu_rows,
)


class FakeCursor:
    def __init__(self):
        self.fetchone_value = None
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.fetchone_value


class WhatsAppClientReceptionTests(unittest.TestCase):
    def test_feature_flag_is_off_by_default(self):
        previous = os.environ.pop("WHATSAPP_CLIENT_RECEPTION_ENABLED", None)
        try:
            self.assertFalse(client_reception_enabled())
            os.environ["WHATSAPP_CLIENT_RECEPTION_ENABLED"] = "true"
            self.assertTrue(client_reception_enabled())
        finally:
            if previous is None:
                os.environ.pop("WHATSAPP_CLIENT_RECEPTION_ENABLED", None)
            else:
                os.environ["WHATSAPP_CLIENT_RECEPTION_ENABLED"] = previous

    def test_menu_has_all_seven_public_services(self):
        rows = reception_menu_rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["id"], "client_existing")
        self.assertEqual(rows[-1]["id"], "client_contact")

    def test_menu_disclaims_automated_legal_advice(self):
        message = reception_menu()
        self.assertIn("registered", message.lower())
        self.assertIn("does not provide automated legal advice", message.lower())

    def test_text_and_interactive_actions_classify(self):
        self.assertEqual(classify_client_command("Hi"), ("MENU", ""))
        self.assertEqual(
            classify_client_command("ignored", "client_appointment"),
            ("APPOINTMENT", ""),
        )
        self.assertEqual(
            classify_client_command("ignored", "client_case:42"),
            ("CASE_SELECTED", "42"),
        )
        self.assertEqual(
            classify_client_command("Need advice about property"),
            ("MESSAGE", "Need advice about property"),
        )

    def test_case_list_uses_internal_id_but_displays_number_and_title(self):
        rows = _case_rows([{
            "id": 42,
            "case_number": "CS/3712/2018",
            "case_title": "A v B",
        }])
        self.assertEqual(rows[0]["id"], "client_case:42")
        self.assertEqual(rows[0]["title"], "CS/3712/2018")
        self.assertEqual(rows[0]["description"], "A v B")

    def test_selected_case_is_denied_when_not_registered_for_phone(self):
        with patch(
            "services.whatsapp_client_reception._registered_cases",
            return_value=[{"id": 9, "case_number": "CS/9"}],
        ):
            self.assertIsNone(_selected_registered_case(FakeCursor(), "919999999999", "42"))

    def test_selected_case_returns_only_registered_row(self):
        expected = {"id": 42, "case_number": "CS/42"}
        with patch(
            "services.whatsapp_client_reception._registered_cases",
            return_value=[expected],
        ):
            self.assertEqual(
                _selected_registered_case(FakeCursor(), "919999999999", "42"),
                expected,
            )

    def test_case_summary_is_limited_and_warns_not_legal_advice(self):
        summary = _case_summary({
            "case_title": "A v B", "case_number": "CS/42",
            "next_hearing": "2026-10-10", "status": "Pending",
        })
        self.assertIn("A v B", summary)
        self.assertIn("2026-10-10", summary)
        self.assertIn("not legal advice", summary)

    def test_office_profile_messages_use_environment_values(self):
        with patch.dict(os.environ, {
            "OFFICE_NAME": "Law Office Test",
            "OFFICE_HOURS": "10 AM-6 PM",
            "COURT_OFFICE_ADDRESS": "Chamber 247",
            "OFFICE_PHONE_NUMBER": "+91 12345",
            "OFFICE_EMAIL": "office@example.test",
        }, clear=False):
            self.assertIn("Chamber 247", _location_message())
            self.assertIn("10 AM-6 PM", _location_message())
            self.assertIn("+91 12345", _contact_message())
            self.assertIn("office@example.test", _contact_message())


if __name__ == "__main__":
    unittest.main()
