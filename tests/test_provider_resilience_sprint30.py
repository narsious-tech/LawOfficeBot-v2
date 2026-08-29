import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests

from advocate_diaries import AdvocateDiaries
from advocate_web import AdvocateWeb
from commands import attendance


class ProviderResilienceTests(unittest.TestCase):
    def test_advocate_web_retries_safe_read(self):
        web = AdvocateWeb()
        expected = Mock()
        web.session.request = Mock(
            side_effect=[requests.ConnectionError("reset"), expected]
        )
        with patch("advocate_web.time.sleep", return_value=None):
            result = web._request("GET", "https://example.test", retry_read=True)

        self.assertIs(result, expected)
        self.assertEqual(web.session.request.call_count, 2)

    def test_advocate_api_retries_safe_read(self):
        expected = Mock()
        with patch("advocate_diaries.time.sleep", return_value=None), patch(
            "advocate_diaries.requests.get",
            side_effect=[requests.Timeout("slow"), expected],
        ) as request_get:
            result = AdvocateDiaries._read("https://example.test")

        self.assertIs(result, expected)
        self.assertEqual(request_get.call_count, 2)

    def test_attendance_outage_is_contained(self):
        context = Mock()
        context.bot.send_message = AsyncMock()

        with patch.object(
            attendance.web,
            "attendance",
            side_effect=requests.ConnectionError("provider unavailable"),
        ):
            asyncio.run(attendance.monitor_attendance_job(context))

        context.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
