from datetime import date
import unittest

from services.staff_hearing_service import hearing_message_chunks


class StaffHearingServiceTests(unittest.TestCase):
    def test_empty_hearing_day_is_clear(self):
        messages = hearing_message_chunks({
            "date": date(2026, 9, 26), "source": "API", "groups": [], "total": 0,
        })
        self.assertEqual(len(messages), 1)
        self.assertIn("No hearings are listed", messages[0])

    def test_hearing_output_contains_title_number_and_stage(self):
        messages = hearing_message_chunks({
            "date": date(2026, 9, 26),
            "source": "API",
            "total": 1,
            "groups": [{
                "court_name": "District Court",
                "judge_name": "Court 1",
                "floor": "2",
                "room": "4",
                "cases": [{
                    "case_title": "A vs B",
                    "case_number": "CS/1/2026",
                    "stage": "Evidence",
                }],
            }],
        })
        text = "\n".join(messages)
        self.assertIn("A vs B", text)
        self.assertIn("CS/1/2026", text)
        self.assertIn("Evidence", text)
        self.assertIn("District Court", text)


if __name__ == "__main__":
    unittest.main()
