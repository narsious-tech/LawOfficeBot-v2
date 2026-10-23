"""Offline attendance, owner authorization and read-only route regressions."""
from datetime import date, datetime, timezone, timedelta
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from flask import Flask
from services.ios_office_dashboard import register_ios_office_dashboard

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'a' * 43
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 23, 14, 30, tzinfo=IST)


def module(name, **attrs):
    obj = types.ModuleType(name)
    obj.__dict__.update(attrs)
    return obj


def session(**attrs):
    return {'staff_name': 'Example Staff', 'status': 'OPEN',
            'checkin_time': datetime(2026, 10, 23, 9, 0), 'checkout_time': None,
            'checkin_office_name': 'Morning Office', 'current_office_name': 'Evening Office',
            'checkout_office_name': None, 'working_minutes': None, **attrs}


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('actual_attendance_report', ROOT / 'services/ios_attendance_reports.py')
        self.report = importlib.util.module_from_spec(spec)
        with patch('zoneinfo.ZoneInfo', return_value=IST):
            spec.loader.exec_module(self.report)
        self.clock = patch.object(self.report, '_now', return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()

    def build(self, rows):
        with patch.object(self.report, '_read_sessions', return_value=rows) as read:
            result = self.report.build_who_in_office_report()
        read.assert_called_once_with(date(2026, 10, 23))
        return result

    def test_working_uses_current_office_and_elapsed_time(self):
        result = self.build([session()])
        self.assertEqual(result['counts']['working'], 1)
        self.assertEqual(result['counts']['checked_out'], 0)
        self.assertIn('Current recorded office: Evening Office', result['summary'])
        self.assertIn('5h 30m', result['summary'])
        self.assertIn('09:00 AM', result['summary'])

    def test_checkout_and_aware_timestamp(self):
        result = self.build([session(status='CLOSED',
            checkin_time=datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc),
            checkout_time=datetime(2026, 10, 23, 7, 30, tzinfo=timezone.utc),
            checkout_office_name='Evening Office')])
        self.assertEqual(result['counts']['checked_out'], 1)
        self.assertEqual(result['counts']['working'], 0)
        self.assertIn('4h 0m', result['summary'])
        self.assertIn('Out: 01:00 PM', result['summary'])

    def test_empty_source_does_not_infer_absence(self):
        result = self.build([])
        self.assertTrue(result['ok'])
        self.assertEqual(result['counts']['working'], 0)
        self.assertIn('No attendance sessions recorded', result['summary'])
        self.assertIn('No check-in does not prove absence', result['summary'])

    def test_inconsistent_and_future_records_are_not_counted_as_working(self):
        rows = [session(checkin_time=None), session(checkout_time=NOW),
                session(status='CLOSED'), session(status='UNKNOWN'),
                session(checkin_time=NOW + timedelta(hours=1)),
                session(status='CLOSED', checkout_time=datetime(2026, 10, 23, 8)),
                session(checkout_time='invalid')]
        result = self.build(rows)
        self.assertEqual(result['counts']['working'], 0)
        self.assertEqual(result['counts']['checked_out'], 0)
        self.assertEqual(result['counts']['needs_verification'], 7)
        self.assertTrue(result['warnings'])

    def test_cancelled_excluded_and_old_schema_office_fallback(self):
        result = self.build([session(status='CANCELLED'), session(current_office_name=None)])
        self.assertEqual(result['counts']['cancelled'], 1)
        self.assertEqual(result['counts']['working'], 1)
        self.assertIn('Current recorded office: Morning Office', result['summary'])

    def test_no_location_coordinates_or_ids_returned(self):
        result = self.build([session(telegram_user_id=999999, latitude='SECRET_LAT',
            longitude='SECRET_LON', map_link='SECRET_MAP')])
        for private in ('999999', 'SECRET_LAT', 'SECRET_LON', 'SECRET_MAP'):
            self.assertNotIn(private, str(result))

    def test_reader_is_select_only_date_parameterized_and_closes(self):
        con = MagicMock()
        cur = con.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [session()]
        driver = module('psycopg2', connect=Mock(return_value=con))
        with patch.dict(sys.modules, {'psycopg2': driver,
             'psycopg2.extras': module('extras', RealDictCursor=object)}), \
             patch.dict(os.environ, {'DATABASE_URL': 'synthetic'}):
            rows = self.report._read_sessions(date(2026, 10, 23))
        self.assertEqual(len(rows), 1)
        con.set_session.assert_called_once_with(readonly=True)
        con.close.assert_called_once_with()
        con.commit.assert_not_called()
        sql, values = cur.execute.call_args.args
        self.assertTrue(sql.lstrip().startswith('SELECT'))
        self.assertEqual(values, (date(2026, 10, 23),))
        self.assertIn("to_jsonb(s)->>'current_office_name'", sql)
        self.assertNotIn('latitude', sql)

    def test_read_failure_closes_and_does_not_return_false_empty(self):
        con = MagicMock()
        con.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError('DB unavailable')
        driver = module('psycopg2', connect=Mock(return_value=con))
        with patch.dict(sys.modules, {'psycopg2': driver,
             'psycopg2.extras': module('extras', RealDictCursor=object)}), \
             patch.dict(os.environ, {'DATABASE_URL': 'synthetic'}):
            with self.assertRaises(RuntimeError):
                self.report._read_sessions(date(2026, 10, 23))
        con.close.assert_called_once_with()


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'IOS_OFFICE_DASHBOARD_ENABLED': 'true',
            'IOS_OFFICE_DASHBOARD_TOKEN': TOKEN, 'ADMIN_USER_ID': '12345'}, clear=True)
        self.env.start()
        self.profile = Mock(return_value={'is_active': True})
        self.builder = Mock(return_value={'ok': True, 'summary': 'WHO IS IN OFFICE'})
        self.modules = patch.dict(sys.modules, {
            'services.role_intelligence_service': module('role', staff_profile=self.profile),
            'services.ios_attendance_reports': module('attendance', build_who_in_office_report=self.builder)})
        self.modules.start()
        self.app = Flask(__name__)
        register_ios_office_dashboard(self.app)
        self.client = self.app.test_client()
        self.url = '/api/ios/who-in-office'
        self.auth = {'Authorization': 'Bearer ' + TOKEN}

    def tearDown(self):
        self.modules.stop()
        self.env.stop()

    def test_owner_success_cache_and_prior_routes_preserved(self):
        r = self.client.get(self.url, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['summary'], 'WHO IS IN OFFICE')
        self.assertEqual(r.headers['Cache-Control'], 'no-store, private')
        self.assertEqual(r.headers['Vary'], 'Authorization')
        self.assertNotIn('Access-Control-Allow-Origin', r.headers)
        self.profile.assert_called_once_with(12345, read_only=True)
        self.builder.assert_called_once_with()
        rules = {r.rule for r in self.app.url_map.iter_rules()}
        for path in ('office-dashboard', 'today-hearings', 'morning-dashboard', 'evening-dashboard',
                     'office-tasks', 'overdue-tasks', 'due-today-tasks', 'task-deadlines'):
            self.assertIn('/api/ios/' + path, rules)

    def test_auth_and_disabled_configuration_before_reads(self):
        for auth in ('', 'Basic abc', 'Bearer wrong', 'Bearer café'):
            self.assertEqual(self.client.get(self.url, headers={'Authorization': auth}).status_code, 401)
        for key, value in [('IOS_OFFICE_DASHBOARD_ENABLED', 'false'),
                           ('IOS_OFFICE_DASHBOARD_TOKEN', ''), ('ADMIN_USER_ID', '-123')]:
            with patch.dict(os.environ, {key: value}):
                self.assertEqual(self.client.get(self.url, headers=self.auth).status_code, 503)
        self.profile.assert_not_called()
        self.builder.assert_not_called()

    def test_no_writes_or_query_impersonation(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
            r = self.client.open(self.url, method=method, headers=self.auth)
            self.assertEqual(r.status_code, 405)
            self.assertEqual(r.headers['Allow'], 'GET')
        for query in ('?user_id=9', '?date=2020-01-01', '?token=' + TOKEN):
            self.assertEqual(self.client.get(self.url + query, headers=self.auth).status_code, 400)
        self.profile.assert_not_called()
        self.builder.assert_not_called()

    def test_inactive_and_redacted_source_errors(self):
        self.profile.return_value = {'is_active': False}
        self.assertEqual(self.client.get(self.url, headers=self.auth).status_code, 403)
        self.builder.assert_not_called()
        self.profile.return_value = {'is_active': True}
        self.builder.side_effect = RuntimeError('SECRET_DATABASE_PASSWORD')
        with self.assertLogs(self.app.logger, level='ERROR') as logs:
            r = self.client.get(self.url, headers=self.auth)
        self.assertEqual(r.status_code, 503)
        self.assertIn('summary', r.json)
        self.assertNotIn('SECRET', r.text + ''.join(logs.output))


if __name__ == '__main__':
    unittest.main()
