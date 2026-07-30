import unittest
from datetime import date

from services.ecourts_date_rules import classify_dates


class DateVerificationTests(unittest.TestCase):
    def test_matching_dates_are_verified(self):
        self.assertEqual(
            classify_dates("2026-08-12", date(2026, 8, 12), today="2026-07-30")[0],
            "VERIFIED",
        )

    def test_different_fresh_dates_create_conflict(self):
        self.assertEqual(
            classify_dates(
                "2026-08-10",
                "2026-08-12",
                "2026-07-24",
                "2026-07-24",
                today="2026-07-30",
            )[0],
            "DATE_CONFLICT",
        )

    def test_older_ecourts_snapshot_never_overrides_staff(self):
        self.assertEqual(
            classify_dates(
                "2026-08-10",
                "2026-07-30",
                "2026-07-25",
                "2026-07-24",
                today="2026-07-30",
            )[0],
            "AWAITING_ECOURTS",
        )

    def test_historical_difference_does_not_require_admin_decision(self):
        self.assertEqual(
            classify_dates(
                "2023-07-13",
                "2023-09-25",
                today="2026-07-30",
            )[0],
            "HISTORICAL_STALE",
        )

    def test_historical_ecourts_date_does_not_challenge_future_staff_date(self):
        self.assertEqual(
            classify_dates(
                "2026-08-10",
                "2023-09-25",
                today="2026-07-30",
            )[0],
            "AWAITING_ECOURTS",
        )

    def test_future_ecourts_date_can_correct_historical_staff_date(self):
        self.assertEqual(
            classify_dates(
                "2023-07-13",
                "2026-08-10",
                today="2026-07-30",
            )[0],
            "DATE_CONFLICT",
        )


if __name__ == "__main__":
    unittest.main()
