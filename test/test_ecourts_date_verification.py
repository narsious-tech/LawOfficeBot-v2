import unittest
from datetime import date

from services.ecourts_date_rules import classify_dates


class DateVerificationTests(unittest.TestCase):
    def test_matching_dates_are_verified(self):
        self.assertEqual(
            classify_dates("2026-08-12", date(2026, 8, 12))[0],
            "VERIFIED",
        )

    def test_different_fresh_dates_create_conflict(self):
        self.assertEqual(
            classify_dates("2026-08-10", "2026-08-12", "2026-07-24", "2026-07-24")[0],
            "DATE_CONFLICT",
        )

    def test_older_ecourts_snapshot_never_overrides_staff(self):
        self.assertEqual(
            classify_dates("2026-08-10", "2026-07-30", "2026-07-25", "2026-07-24")[0],
            "AWAITING_ECOURTS",
        )


if __name__ == "__main__":
    unittest.main()
