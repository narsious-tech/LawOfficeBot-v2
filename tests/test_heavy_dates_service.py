import unittest
from datetime import date

from services.heavy_dates_service import (
    analyse_heavy_dates, parse_hearing_date, render_heavy_date_detail,
    render_heavy_dates,
)


SETTINGS = {
    "lookahead_days": 45,
    "minimum_matters": 8,
    "minimum_same_purpose": 3,
    "very_heavy_matters": 12,
    "very_heavy_purpose": 6,
}


class HeavyDatesServiceTests(unittest.TestCase):
    def test_mixed_hearing_dates_are_parsed(self):
        self.assertEqual(parse_hearing_date("2026-11-15"), date(2026, 11, 15))
        self.assertEqual(parse_hearing_date("15-11-2026"), date(2026, 11, 15))
        self.assertEqual(parse_hearing_date("15/11/2026"), date(2026, 11, 15))

    def test_repeated_purpose_marks_date_heavy_below_total_threshold(self):
        rows = [
            {
                "case_number": f"CS/{index}/2026",
                "case_title": f"Matter {index}",
                "next_hearing": "2026-11-15",
                "next_purpose": purpose,
                "status": "OPEN",
            }
            for index, purpose in enumerate(("Evidence", "For Evidence", "evidence", "Arguments"), 1)
        ]
        items = analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["total"], 4)
        self.assertEqual(items[0]["largest_purpose_cluster"], 3)

    def test_closed_cases_and_dates_outside_window_are_ignored(self):
        rows = [
            {"case_number": str(i), "next_hearing": "2026-11-15", "next_purpose": "Evidence", "status": "DISPOSED"}
            for i in range(10)
        ]
        rows += [
            {"case_number": str(i), "next_hearing": "2027-01-15", "next_purpose": "Evidence", "status": "OPEN"}
            for i in range(10, 20)
        ]
        self.assertEqual(analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS), [])

    def test_render_includes_case_numbers_and_purpose(self):
        rows = [
            {"case_number": f"CS/{i}/2026", "next_hearing": "2026-11-15", "next_purpose": "Evidence", "status": "OPEN"}
            for i in range(1, 4)
        ]
        items = analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS)
        text = render_heavy_date_detail(items[0])
        self.assertIn("Evidence — 3", text)
        self.assertIn("CS/1/2026", text)

    def test_duplicate_case_rows_are_counted_once(self):
        rows = [
            {"case_number": "CRM/1790/2026", "next_hearing": "2026-11-06", "next_purpose": "", "status": "OPEN"},
            {"case_number": "CRM/1790/2026", "next_hearing": "06-11-2026", "next_purpose": "Arguments", "status": "OPEN"},
            {"case_number": "CS/1/2026", "next_hearing": "06-11-2026", "next_purpose": "Arguments", "status": "OPEN"},
            {"case_number": "CS/2/2026", "next_hearing": "06-11-2026", "next_purpose": "Arg", "status": "OPEN"},
        ]
        items = analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS)
        self.assertEqual(items[0]["total"], 3)
        self.assertEqual(items[0]["largest_purpose_cluster"], 3)

    def test_missing_purpose_is_warning_not_shared_purpose(self):
        rows = [
            {"case_number": f"CS/{i}/2026", "next_hearing": "2026-11-07", "next_purpose": "", "status": "OPEN"}
            for i in range(1, 4)
        ]
        items = analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS)
        self.assertEqual(items, [])

    def test_compact_report_does_not_list_every_case(self):
        rows = [
            {"case_number": f"CS/{i}/2026", "next_hearing": "2026-11-15", "next_purpose": "Evidence", "status": "OPEN"}
            for i in range(1, 9)
        ]
        items = analyse_heavy_dates(rows, start_date=date(2026, 11, 3), **SETTINGS)
        text = render_heavy_dates(items, lookahead_days=45)
        self.assertIn("Evidence 8", text)
        self.assertNotIn("CS/1/2026", text)



if __name__ == "__main__":
    unittest.main()
