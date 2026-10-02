"""Regression coverage for RealDictCursor results in notification helpers."""
import importlib.util
import ast
import asyncio
from collections import defaultdict
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch
from unittest.mock import AsyncMock


class Cursor:
    def __init__(self, one=None, batches=None):
        self.one = one
        self.batches = iter(batches or [])
        self.query = ""

    def execute(self, query, params=None):
        self.query = query

    def fetchone(self):
        return self.one

    def fetchall(self):
        return next(self.batches)


class NotificationDictRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = types.ModuleType('psycopg2')
        extras = types.ModuleType('psycopg2.extras')
        extras.RealDictCursor = object
        config = types.ModuleType('config')
        config.DATABASE_URL = 'postgresql://unused'
        cloud = types.ModuleType('services.whatsapp_cloud')
        def normalize(value):
            if not value:
                raise ValueError('missing phone')
            return '91' + value if len(value) == 10 else value
        cloud.normalize_phone = normalize
        cloud.send_template_message = lambda *args: None
        cloud.transport_ready = lambda: False
        path = Path(__file__).resolve().parents[1] / 'services/whatsapp_case_notifications.py'
        spec = importlib.util.spec_from_file_location('notification_db_under_test', path)
        cls.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            'psycopg2': db, 'psycopg2.extras': extras, 'config': config,
            'services.whatsapp_cloud': cloud,
        }):
            spec.loader.exec_module(cls.service)

    def test_month_spend_reads_named_decimal_column(self):
        cursor = Cursor(one={'month_spend': Decimal('17.2500')})
        self.assertEqual(self.service._month_spend(cursor), Decimal('17.2500'))
        self.assertIn('AS month_spend', cursor.query)

    def test_month_spend_zero(self):
        self.assertEqual(self.service._month_spend(Cursor(one={'month_spend': 0})), Decimal(0))

    def claim(self, result):
        return self.service._claim_event(
            Cursor(one=result), event_key='event', row={'case_db_id': 1},
            event_type='D2_REMINDER', hearing=None, template='test', body='test',
            rate=Decimal('0.115'),
        )

    def test_claim_reads_named_returning_id(self):
        self.assertEqual(self.claim({'id': 42}), 42)

    def test_duplicate_claim_returns_none(self):
        self.assertIsNone(self.claim(None))

    def test_consent_override_reads_named_rows_after_phone_normalization(self):
        cursor = Cursor(batches=[
            [{'case_db_id': 1, 'phone_number': '9876543210', 'consent_status': 'UNKNOWN'}],
            [{'phone_number': '919876543210', 'consent_status': 'OPTED_OUT'}],
        ])
        rows = self.service._case_rows(cursor)
        self.assertEqual(rows[0]['phone_number'], '919876543210')
        self.assertEqual(rows[0]['consent_status'], 'OPTED_OUT')

    def test_manual_sync_runs_before_notification_warning_is_built(self):
        path = Path(__file__).resolve().parents[1] / 'commands/ad_sync_v3.py'
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if getattr(node, 'name', '') == 'synccasesv3')
        function.args.args[0].annotation = None
        function.args.args[1].annotation = None
        reply = AsyncMock()
        update = types.SimpleNamespace(effective_message=types.SimpleNamespace(reply_text=reply))
        result = defaultdict(int)
        scan = AsyncMock(return_value={
            'sent': 0, 'duplicates': 0, 'failed': 0, 'scan_error': 'test scan failure',
        })
        namespace = {
            'asyncio': asyncio, 'run_sync_v3': lambda: result,
            '_post_sync_notification_scan': scan,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        asyncio.run(namespace['synccasesv3'](update, None))
        self.assertEqual(reply.await_count, 2)
        self.assertIn('SYNC v3 COMPLETED', reply.await_args.args[0])
        self.assertIn('test scan failure', reply.await_args.args[0])


if __name__ == '__main__':
    unittest.main()
