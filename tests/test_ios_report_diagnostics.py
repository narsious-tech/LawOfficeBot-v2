import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


class DiagnosticTests(unittest.TestCase):
    def test_step_redacts_exception_text(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('diagnostic_report', root / 'services/ios_court_reports.py')
        obj = importlib.util.module_from_spec(spec)
        from datetime import timezone
        with patch('zoneinfo.ZoneInfo', return_value=timezone.utc):
            spec.loader.exec_module(obj)

        def fail():
            raise RuntimeError('SECRET_PASSWORD')

        with self.assertLogs('diagnostic_report', level='INFO') as captured:
            with self.assertRaises(RuntimeError):
                obj._step('cause_list_fetch', fail)
        logs = '\n'.join(captured.output)
        self.assertIn('cause_list_fetch', logs)
        self.assertIn('RuntimeError', logs)
        self.assertNotIn('SECRET_PASSWORD', logs)

    def test_step_preserves_result(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('diagnostic_report', root / 'services/ios_court_reports.py')
        obj = importlib.util.module_from_spec(spec)
        from datetime import timezone
        with patch('zoneinfo.ZoneInfo', return_value=timezone.utc):
            spec.loader.exec_module(obj)
        self.assertEqual(obj._step('cause_list_format', lambda value: value, 42), 42)
