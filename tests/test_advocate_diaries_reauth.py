import unittest
from unittest.mock import Mock, patch

import advocate_diaries
from advocate_diaries import AdvocateDiaries


def response(status, payload):
    item = Mock()
    item.status_code = status
    item.json.return_value = payload
    item.raise_for_status.side_effect = None
    return item


class AdvocateDiariesReauthTests(unittest.TestCase):
    def test_daily_cause_list_reauthenticates_once_after_401(self):
        login_one = response(200, {
            "success": True,
            "data": {"access_token": "first", "refresh_token": "r1"},
        })
        login_two = response(200, {
            "success": True,
            "data": {"access_token": "second", "refresh_token": "r2"},
        })
        unauthorized = response(401, {"message": "Unauthenticated"})
        success = response(200, {"success": True, "data": {"groups": []}})

        with patch.object(advocate_diaries, "BASE_URL", "https://example.test"), \
             patch.object(advocate_diaries, "EMAIL", "office@example.test"), \
             patch.object(advocate_diaries, "PASSWORD", "secret"), \
             patch("advocate_diaries.requests.post", side_effect=[login_one, login_two]), \
             patch("advocate_diaries.requests.get", side_effect=[unauthorized, success]) as get:
            result = AdvocateDiaries().daily_cause_list("2026-09-26")

        self.assertTrue(result["success"])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            get.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer second",
        )


if __name__ == "__main__":
    unittest.main()
