import unittest

from services.staff_activity_service import redact_message_text


class StaffActivityRedactionTests(unittest.TestCase):
    def test_linkstaff_credentials_are_never_retained(self):
        result = redact_message_text(
            "/linkstaff Happy happy@example.com super-secret-password"
        )
        self.assertEqual(result, "/linkstaff [CREDENTIALS REDACTED]")
        self.assertNotIn("super-secret-password", result)

    def test_normal_office_message_is_preserved(self):
        self.assertEqual(
            redact_message_text("File has been brought to court"),
            "File has been brought to court",
        )


if __name__ == "__main__":
    unittest.main()
