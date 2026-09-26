"""WhatsApp dashboard pagination and protected live-status controls."""
import sys
import types
import unittest

from services.whatsapp_dashboard_service import (
    apply_live_status,
    live_board,
    live_confirmation,
    live_detail,
)


class WhatsAppDashboardTests(unittest.TestCase):
    def setUp(self):
        self.updated = []
        self.hearings = [
            {
                "id": index,
                "case_number": f"CS/{index}/2026",
                "case_title": f"Case {index}",
                "stage": "Arguments",
                "judge_name": "Court",
                "court_name": "District Court",
                "floor": "1",
                "room": str(index),
                "status": "LISTED",
            }
            for index in range(1, 11)
        ]
        service = types.ModuleType("services.live_hearing_service")
        service.list_live_hearings = lambda: list(self.hearings)
        service.sync_live_hearings = lambda: (len(self.hearings), "test")
        service.get_live_hearing = lambda hearing_id: next(
            (dict(row) for row in self.hearings if row["id"] == hearing_id), None
        )

        def update(hearing_id, status, changed_by):
            row = service.get_live_hearing(hearing_id)
            if row:
                row["status"] = status
                self.updated.append((hearing_id, status, changed_by))
            return row

        service.set_live_hearing_status = update
        self.original = sys.modules.get("services.live_hearing_service")
        sys.modules["services.live_hearing_service"] = service

    def tearDown(self):
        if self.original is None:
            sys.modules.pop("services.live_hearing_service", None)
        else:
            sys.modules["services.live_hearing_service"] = self.original

    def test_live_board_uses_eight_hearings_and_next_page(self):
        board = live_board()
        self.assertEqual(len(board["rows"]), 9)
        self.assertEqual(board["rows"][-1]["id"], "owner_live_page:1")
        self.assertIn("Page 1 of 2", board["reply"])

    def test_live_detail_offers_safe_statuses_but_not_disposal(self):
        detail = live_detail(1)
        ids = [row["id"] for row in detail["rows"]]
        self.assertTrue(any(":ADJOURNED:" in action for action in ids))
        self.assertFalse(any(":DISPOSED:" in action for action in ids))

    def test_live_change_requires_confirmation_before_write(self):
        confirmation = live_confirmation(1, "CALLED")
        self.assertEqual(self.updated, [])
        self.assertEqual(confirmation["buttons"][0][0], "owner_live_confirm:1:CALLED:0")
        result = apply_live_status(1, "CALLED", 123)
        self.assertEqual(self.updated, [(1, "CALLED", 123)])
        self.assertIn("UPDATED", result["reply"])


if __name__ == "__main__":
    unittest.main()
