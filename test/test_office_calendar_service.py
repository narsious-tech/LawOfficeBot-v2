import unittest
from datetime import date

from services.office_calendar_service import (
    is_evening_office_open,
    is_full_day_saturday_holiday,
    is_morning_office_open,
    is_working_saturday,
    scheduled_evening_plan,
    working_saturday_monday_plan,
)


class OfficeCalendarTests(unittest.TestCase):
    def test_saturday_rotation(self):
        self.assertTrue(is_working_saturday(date(2026, 7, 4)))
        self.assertTrue(is_full_day_saturday_holiday(date(2026, 7, 11)))
        self.assertTrue(is_working_saturday(date(2026, 7, 18)))
        self.assertTrue(is_full_day_saturday_holiday(date(2026, 7, 25)))
        self.assertTrue(is_working_saturday(date(2026, 8, 29)))

    def test_saturday_evening_always_closed(self):
        self.assertFalse(is_evening_office_open(date(2026, 7, 4)))
        self.assertFalse(is_evening_office_open(date(2026, 7, 25)))

    def test_sunday_schedule(self):
        sunday = date(2026, 7, 26)
        self.assertFalse(is_morning_office_open(sunday))
        self.assertTrue(is_evening_office_open(sunday))

    def test_holiday_saturday_finalizes_on_friday(self):
        plan = scheduled_evening_plan(date(2026, 7, 24))
        self.assertEqual(plan.target_date, date(2026, 7, 27))
        self.assertEqual(plan.mode, "MONDAY_FINALIZATION_FRIDAY")

    def test_working_saturday_is_prepared_friday_and_monday_on_saturday(self):
        friday_plan = scheduled_evening_plan(date(2026, 7, 17))
        self.assertEqual(friday_plan.target_date, date(2026, 7, 18))
        saturday_plan = working_saturday_monday_plan(date(2026, 7, 18))
        self.assertEqual(saturday_plan.target_date, date(2026, 7, 20))

    def test_holiday_saturday_has_no_saturday_finalization(self):
        self.assertIsNone(working_saturday_monday_plan(date(2026, 7, 25)))


if __name__ == "__main__":
    unittest.main()
