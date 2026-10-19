import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from services.judge_leave_service import (
    apply_leave_exclusions,
    holiday_greeting,
    judge_signature,
    match_leave_groups,
    normalize,
    pdf_mentions_date,
)


GROUPS = [
    {
        "judge_name": "Sh. Rajiv Kumar, Additional District Judge",
        "court_name": "District Courts Ludhiana",
        "cases": [
            {"case_number": "CS/1/2026", "case_title": "A VS B"},
            {"case_number": "CS/2/2026", "case_title": "C VS D"},
        ],
    },
    {
        "judge_name": "Smt. Neha Sharma, Civil Judge",
        "court_name": "District Courts Ludhiana",
        "cases": [{"case_number": "CS/3/2026", "case_title": "E VS F"}],
    },
]


class JudgeLeaveRulesTests(unittest.TestCase):
    def test_signature_removes_designation_but_keeps_name(self):
        self.assertEqual(judge_signature(GROUPS[0]["judge_name"]), "rajiv kumar")

    def test_pdf_matches_only_known_cause_list_judge(self):
        text = "Officers on leave dated 20-10-2026\n1. Sh. Rajiv Kumar, ADJ"
        matches = match_leave_groups(text, GROUPS)
        self.assertEqual([row["judge_name"] for row in matches], [GROUPS[0]["judge_name"]])
        self.assertEqual(matches[0]["case_count"], 2)

    def test_single_name_is_not_matched_loosely(self):
        groups = [{"judge_name": "Kumar", "court_name": "Court", "cases": [{}]}]
        self.assertEqual(match_leave_groups("Kumar is on leave", groups), [])

    def test_target_date_recognized_in_common_formats(self):
        target = date(2026, 10, 20)
        self.assertTrue(pdf_mentions_date("Leave list dated 20.10.2026", target))
        self.assertTrue(pdf_mentions_date("20 October 2026", target))
        self.assertFalse(pdf_mentions_date("19 October 2026", target))

    def test_confirmed_judge_group_is_excluded_without_mutating_input(self):
        entries = [{"judge_key": normalize(GROUPS[0]["judge_name"]), "judge_name": GROUPS[0]["judge_name"]}]
        kept, excluded, returned = apply_leave_exclusions(GROUPS, date(2026, 10, 20), entries)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["judge_name"], GROUPS[1]["judge_name"])
        self.assertEqual(len(excluded), 2)
        self.assertEqual(len(GROUPS), 2)
        self.assertEqual(returned, entries)

    @patch("services.judge_leave_service.holiday_name", return_value="Diwali (Deepavali)")
    def test_holiday_greeting_requires_two_or_fewer_active_cases(self, _holiday):
        target = date(2026, 11, 8)
        self.assertIn("DIWALI", holiday_greeting(target, 2))
        self.assertIsNone(holiday_greeting(target, 3))


class IntegrationMarkersTests(unittest.TestCase):
    def test_evening_dashboard_applies_leave_exclusions(self):
        source = Path("commands/evening_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("apply_leave_exclusions", source)
        self.assertIn("Judge-leave exclusions", source)

    def test_alert_jobs_are_thirty_minutes_before_file_jobs(self):
        source = Path("bot.py").read_text(encoding="utf-8")
        self.assertIn("time=time(hour=16, minute=0", source)
        self.assertIn("time=time(hour=16, minute=30", source)
        self.assertIn("time=time(hour=12, minute=30", source)
        self.assertIn("time=time(hour=13, minute=0", source)

    def test_whatsapp_webhook_carries_document_metadata(self):
        source = Path("services/whatsapp_cloud.py").read_text(encoding="utf-8")
        self.assertIn('"media_id": media.get("id")', source)
        self.assertIn("def download_media", source)


if __name__ == "__main__":
    unittest.main()
