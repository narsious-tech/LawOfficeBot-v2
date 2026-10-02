import unittest
from datetime import date, datetime
from services.whatsapp_case_notification_rules import (
    is_closed_status,
    is_material_purpose,
    parse_case_date,
)


class WhatsAppCaseNotificationTests(unittest.TestCase):
    def test_supported_case_date_formats(self):
        self.assertEqual(parse_case_date("2026-10-04"), date(2026, 10, 4))
        self.assertEqual(parse_case_date("04-10-2026"), date(2026, 10, 4))
        self.assertEqual(parse_case_date("04/10/2026"), date(2026, 10, 4))
        self.assertEqual(parse_case_date(datetime(2026, 10, 4, 10, 30)), date(2026, 10, 4))

    def test_invalid_date_is_not_due(self):
        self.assertIsNone(parse_case_date("not fixed"))
        self.assertIsNone(parse_case_date("31-02-2026"))

    def test_closed_status_detection(self):
        self.assertTrue(is_closed_status("Case disposed"))
        self.assertTrue(is_closed_status("Withdrawn as settled"))
        self.assertFalse(is_closed_status("Pending arguments"))

    def test_action_required_purpose_is_narrow(self):
        self.assertTrue(is_material_purpose("Personal appearance required"))
        self.assertTrue(is_material_purpose("Bring original documents"))
        self.assertFalse(is_material_purpose("For consideration"))

if __name__ == "__main__":
    unittest.main()
