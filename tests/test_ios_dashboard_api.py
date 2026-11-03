import os
import sys
import types
import unittest
from datetime import date
from unittest.mock import patch


class FakeBlueprint:
    def __init__(self, *args, **kwargs):
        self.logger = types.SimpleNamespace(exception=lambda *args, **kwargs: None)

    def get(self, path):
        return lambda function: function


fake_request = types.SimpleNamespace(headers={}, args={})
flask = types.ModuleType("flask")
flask.Blueprint = FakeBlueprint
flask.jsonify = lambda value: value
flask.request = fake_request
sys.modules.setdefault("flask", flask)

role_service = types.ModuleType("services.role_intelligence_service")
role_service.role_dashboard = lambda user_id: {}
sys.modules.setdefault("services.role_intelligence_service", role_service)

from services import ios_dashboard_api as api  # noqa: E402


class IOSDashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "IOS_DASHBOARD_TOKEN": "private-test-token",
            "ADMIN_USER_ID": "12345",
        })
        self.env.start()
        fake_request.headers = {"Authorization": "Bearer private-test-token"}
        fake_request.args = {}
        self.dashboard = {
            "profile": {"staff_name": "Ajay", "role": "Admin", "is_active": True},
            "works": {"pending": 5, "due_today": 2, "overdue": 1},
            "files": {"pending": 3, "brought": 1, "not_found": 0, "attention": 2},
            "pending_updates": 4,
        }

    def tearDown(self):
        self.env.stop()

    def test_token_is_required(self):
        fake_request.headers = {}
        payload, status = api.ios_heavy_dates()
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    @patch("services.ios_dashboard_api.role_dashboard")
    def test_dashboard_is_owner_only(self, dashboard):
        dashboard.return_value = self.dashboard
        payload, status = api.ios_dashboard()
        self.assertEqual(status, 200)
        self.assertEqual(payload["works"]["pending"], 5)

    @patch("services.heavy_dates_service.get_heavy_dates")
    @patch("services.ios_dashboard_api.role_dashboard")
    def test_heavy_dates_returns_shortcut_display_text(self, dashboard, get_dates):
        dashboard.return_value = self.dashboard
        get_dates.return_value = [{
            "date": date(2026, 11, 4), "total": 25, "missing_purpose": 10,
            "severity": "VERY_HEAVY", "largest_purpose_cluster": 4,
            "purpose_clusters": [], "cases": [],
        }]
        fake_request.args = {"days": "45"}
        payload, status = api.ios_heavy_dates()
        self.assertEqual(status, 200)
        self.assertEqual(payload["heavy_date_count"], 1)
        self.assertIn("25 matters", payload["display_text"])

    @patch("services.heavy_dates_service.get_heavy_dates")
    @patch("services.ios_dashboard_api.role_dashboard")
    def test_heavy_date_detail_returns_cases(self, dashboard, get_dates):
        dashboard.return_value = self.dashboard
        get_dates.return_value = [{
            "date": date(2026, 11, 4), "total": 1, "missing_purpose": 0,
            "severity": "HEAVY", "largest_purpose_cluster": 1,
            "purpose_clusters": [],
            "cases": [{
                "case_number": "BA/3166/2026", "case_title": "Gurmeet v State",
                "purpose": "Notice", "purpose_keys": ["notice"],
            }],
        }]
        fake_request.args = {"date": "04-11-2026"}
        with patch("services.ios_dashboard_api.datetime") as now:
            now.now.return_value.date.return_value = date(2026, 11, 3)
            payload, status = api.ios_heavy_date_detail()
        self.assertEqual(status, 200)
        self.assertIn("BA/3166/2026", payload["display_text"])


if __name__ == "__main__":
    unittest.main()
