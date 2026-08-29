import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.ecourtsindia_api_service import api_enabled


class ECourtsZeroCostModeTests(unittest.TestCase):
    def test_api_stays_off_without_separate_paid_consent(self):
        with patch.dict(os.environ, {
            "ECOURTSINDIA_API_KEY": "configured-key",
            "ECOURTSINDIA_API_ENABLED": "true",
            "ECOURTSINDIA_PAID_API_ALLOWED": "false",
        }, clear=False):
            self.assertFalse(api_enabled())

    def test_api_requires_both_flags_and_key(self):
        with patch.dict(os.environ, {
            "ECOURTSINDIA_API_KEY": "configured-key",
            "ECOURTSINDIA_API_ENABLED": "true",
            "ECOURTSINDIA_PAID_API_ALLOWED": "true",
        }, clear=False):
            self.assertTrue(api_enabled())

    def test_bot_schedules_only_daily_backup_reconciliation(self):
        source = (Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8")
        scheduling = source[source.index("# Low-cost mode:"):]
        self.assertIn("run_daily(\n    ecourts_backup_sync_job", scheduling)
        self.assertNotIn("run_repeating(\n    ecourts_order_inbox_job", scheduling)
        self.assertNotIn("run_daily(\n    ecourts_daily_operations_job", scheduling)
        self.assertIn("ECOURTS_BACKUP_SYNC_HOUR_IST", scheduling)


if __name__ == "__main__":
    unittest.main()
