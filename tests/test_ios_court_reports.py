"""Offline regression tests; synthetic records only, no external requests."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, Mock, patch

from flask import Flask
from services.ios_office_dashboard import register_ios_office_dashboard

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'a' * 43
ROUTES = ('office-dashboard', 'today-hearings', 'morning-dashboard', 'evening-dashboard')
GROUPS = [{'judge_name': 'Example Judge', 'court_name': 'Civil Court', 'floor': '1',
           'room': '2', 'cases': [{'case_number': 'TEST/1/2026', 'case_title': 'Synthetic A vs B',
                                  'stage': 'Evidence', 'previous_date': ''}]}]


def module(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    return result


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'IOS_OFFICE_DASHBOARD_ENABLED': 'true',
            'IOS_OFFICE_DASHBOARD_TOKEN': TOKEN, 'ADMIN_USER_ID': '12345'}, clear=True)
        self.env.start()
        self.profile = Mock(return_value={'is_active': True})
        self.office = Mock(return_value={'profile': {'is_active': True},
                                         'files': {}, 'works': {}, 'pending_updates': 0})
        self.court = Mock(return_value={'ok': True, 'summary': 'COURT'})
        self.morning = Mock(return_value={'ok': True, 'summary': 'MORNING'})
        self.modules = patch.dict(sys.modules, {
            'services.role_intelligence_service': module('role', staff_profile=self.profile,
                                                         role_dashboard=self.office),
            'services.ios_court_reports': module('reports', build_court_report=self.court,
                                                 build_morning_report=self.morning)})
        self.modules.start()
        self.app = Flask(__name__)
        register_ios_office_dashboard(self.app)
        self.client = self.app.test_client()
        self.auth = {'Authorization': 'Bearer ' + TOKEN}

    def tearDown(self):
        self.modules.stop()
        self.env.stop()

    def get(self, path, **kwargs):
        return self.client.get('/api/ios/' + path, **kwargs)

    def test_all_reports_and_office_regression(self):
        for path in ROUTES:
            r = self.get(path, headers=self.auth)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json['ok'])
            self.assertIn('summary', r.json)
            self.assertEqual(r.headers['Cache-Control'], 'no-store, private')
            self.assertEqual(r.headers['Vary'], 'Authorization')
            self.assertNotIn('Access-Control-Allow-Origin', r.headers)
        self.office.assert_called_once_with(12345, read_only=True)
        self.profile.assert_called_with(12345, read_only=True)
        self.court.assert_any_call('today')
        self.court.assert_any_call('evening')
        self.morning.assert_called_once_with()

    def test_authentication_blocks_every_route(self):
        for path in ROUTES:
            for auth in ('', 'Bearer invalid', 'Bearer ' + TOKEN + 'x', 'Basic abc', 'Bearer café'):
                r = self.get(path, headers={'Authorization': auth})
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.headers['WWW-Authenticate'], 'Bearer')
                self.assertIn('no-store', r.headers['Cache-Control'])
        for mock in (self.profile, self.office, self.court, self.morning):
            mock.assert_not_called()

    def test_configuration_fails_closed(self):
        for key, value in [('IOS_OFFICE_DASHBOARD_ENABLED', 'false'),
                           ('IOS_OFFICE_DASHBOARD_TOKEN', ''),
                           ('IOS_OFFICE_DASHBOARD_TOKEN', 'bad token'),
                           ('ADMIN_USER_ID', '-123'), ('ADMIN_USER_ID', '')]:
            with patch.dict(os.environ, {key: value}):
                for path in ROUTES:
                    self.assertEqual(self.get(path, headers=self.auth).status_code, 503)
        self.profile.assert_not_called()
        self.court.assert_not_called()

    def test_methods_and_query_credential_rejection(self):
        for path in ROUTES:
            for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
                r = self.client.open('/api/ios/' + path, method=method, headers=self.auth)
                self.assertEqual(r.status_code, 405)
                self.assertEqual(r.headers['Allow'], 'GET')
            for query in ('?date=2026-01-01', '?telegram_id=999', '?token=' + TOKEN):
                self.assertEqual(self.get(path + query, headers=self.auth).status_code, 400)
        for mock in (self.profile, self.office, self.court, self.morning):
            mock.assert_not_called()

    def test_inactive_admin(self):
        self.profile.return_value = {'is_active': False}
        for path in ROUTES[1:]:
            self.assertEqual(self.get(path, headers=self.auth).status_code, 403)
        self.court.assert_not_called()
        self.morning.assert_not_called()

    def test_sanitized_errors(self):
        self.court.side_effect = RuntimeError('PRIVATE_DATABASE_PASSWORD')
        self.morning.side_effect = RuntimeError('PRIVATE_DATABASE_PASSWORD')
        for path in ROUTES[1:]:
            with self.assertLogs(self.app.logger, level='ERROR') as captured:
                r = self.get(path, headers=self.auth)
            self.assertEqual(r.status_code, 503)
            self.assertNotIn('PRIVATE', r.text)
            self.assertNotIn('PRIVATE', '\n'.join(captured.output))


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.driver = module('psycopg2', connect=Mock())
        self.ext = module('telegram.ext', ContextTypes=Mock())
        self.modules = patch.dict(sys.modules, {
            'psycopg2': self.driver,
            'psycopg2.extras': module('psycopg2.extras', RealDictCursor=object),
            'config': module('config', DATABASE_URL='synthetic-only'),
            'telegram': module('telegram', Update=Mock(), InlineKeyboardButton=Mock(), InlineKeyboardMarkup=Mock()),
            'telegram.ext': self.ext,
            'advocate_web': module('advocate_web', AdvocateWeb=Mock()),
            'advocate_diaries': module('advocate_diaries', AdvocateDiaries=Mock()),
            'pypdf': module('pypdf', PdfReader=Mock()),
        })
        self.modules.start()
        # Use fixed IST offset locally so tests do not require OS timezone files.
        with patch('zoneinfo.ZoneInfo', return_value=timezone(timedelta(hours=5, minutes=30))):
            self.calendar = load('services.office_calendar_service', 'services/office_calendar_service.py')
            load('services.staff_motivation_service', 'services/staff_motivation_service.py')
            self.leave = load('services.judge_leave_service', 'services/judge_leave_service.py')
            self.dashboard = load('commands.dashboard', 'commands/dashboard.py')
            self.reports = load('services.ios_court_reports', 'services/ios_court_reports.py')
        self.frozen = type('Frozen', (datetime,), {'now': classmethod(
            lambda cls, tz=None: datetime(2026, 10, 23, 12, 0, tzinfo=tz))})
        self.clock = patch.object(self.reports, 'datetime', self.frozen)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.modules.stop()

    def test_cause_list_uses_existing_reader_and_formatter(self):
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=(GROUPS, 'PDF')) as fetch, \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[]) as entries:
            report = self.reports.build_court_report('today')
        fetch.assert_called_once_with(date(2026, 10, 23))
        entries.assert_called_once_with(date(2026, 10, 23), read_only=True)
        self.assertEqual(report['hearings'], 1)
        self.assertEqual(report['court_date'], '2026-10-23')
        for text in ('TEST/1/2026', 'Synthetic A vs B', 'Evidence', 'Example Judge', 'ROOM 2', 'FLOOR 1'):
            self.assertIn(text, report['summary'])

    def test_evening_uses_calendar_not_calendar_tomorrow(self):
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=(GROUPS, 'API')) as fetch, \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[]):
            report = self.reports.build_court_report('evening')
        # 24 October is the fourth Saturday: existing calendar chooses Monday.
        fetch.assert_called_once_with(date(2026, 10, 26))
        self.assertEqual(report['court_date'], '2026-10-26')
        self.assertIn('physical-file finalization', report['summary'])
        self.assertIn('Synthetic A vs B', report['summary'])
        self.assertIn('Evening reflection', report['summary'])
        self.assertEqual(self.calendar.manual_evening_plan(date(2026, 10, 22)).target_date, date(2026, 10, 23))
        self.assertEqual(self.calendar.manual_evening_plan(date(2026, 10, 17)).target_date, date(2026, 10, 19))
        self.assertEqual(self.calendar.manual_evening_plan(date(2026, 10, 18)).target_date, date(2026, 10, 19))

    def test_confirmed_leave_and_empty_list(self):
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=(GROUPS, 'PDF')), \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[{'judge_key': 'example judge'}]):
            report = self.reports.build_court_report('today')
        self.assertEqual(report['hearings'], 0)
        self.assertEqual(report['excluded_hearings'], 1)
        self.assertNotIn('TEST/1/2026', report['summary'])
        self.assertIn('No hearings', report['summary'])
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=([], 'API')), \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[]):
            self.assertEqual(self.reports.build_court_report('today')['hearings'], 0)

    def test_no_ten_case_truncation(self):
        groups = [{**GROUPS[0], 'cases': [{**GROUPS[0]['cases'][0], 'case_number': f'TEST/{i}/2026'} for i in range(25)]}]
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=(groups, 'PDF')), \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[]):
            report = self.reports.build_court_report('evening')
        self.assertEqual(report['hearings'], 25)
        self.assertIn('TEST/24/2026', report['summary'])

    def test_morning_reuses_actual_builder_with_readonly(self):
        with patch.object(self.dashboard, '_load_morning_dashboard_database', return_value=([], [], [])) as db, \
             patch.object(self.dashboard, '_load_command_centre_metrics', return_value=({}, [])) as metrics, \
             patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', return_value=(GROUPS, 'PDF')), \
             patch.object(self.leave, 'confirmed_leave_entries', return_value=[]) as entries:
            report = self.reports.build_morning_report()
        self.assertTrue(report['ok'])
        self.assertIn('Hearings 1', report['summary'])
        self.assertTrue(db.call_args.kwargs['read_only'])
        self.assertTrue(metrics.call_args.kwargs['read_only'])
        self.assertTrue(entries.call_args.kwargs['read_only'])

    def test_actual_db_loaders_readonly_and_connection_close(self):
        for loader in (self.dashboard._load_morning_dashboard_database,
                       self.dashboard._load_command_centre_metrics):
            con = MagicMock()
            con.cursor.return_value.fetchall.return_value = []
            self.driver.connect.return_value = con
            loader(date(2026, 10, 23), read_only=True)
            con.set_session.assert_called_once_with(readonly=True)
            con.close.assert_called_once_with()
            for call in con.cursor.return_value.execute.call_args_list:
                self.assertTrue(call.args[0].lstrip().startswith('SELECT'))
            con.reset_mock()
            loader(date(2026, 10, 23))
            con.set_session.assert_not_called()

    def test_leave_reader_skips_ddl_and_retains_bot_default(self):
        con = MagicMock()
        cur = con.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        self.driver.connect.return_value = con
        with patch.object(self.leave, 'ensure_schema') as schema:
            self.leave.confirmed_leave_entries(date(2026, 10, 23), read_only=True)
            schema.assert_not_called()
            con.set_session.assert_called_once_with(readonly=True)
            self.assertTrue(cur.execute.call_args.args[0].lstrip().startswith('SELECT'))
            self.leave.confirmed_leave_entries(date(2026, 10, 23))
            schema.assert_called_once_with(cur)

    def test_upstream_failure_is_not_an_empty_list(self):
        with patch.object(self.dashboard, 'fetch_advocate_diaries_cause_groups', side_effect=RuntimeError('upstream')):
            with self.assertRaises(RuntimeError):
                self.reports.build_court_report('today')

    def test_pdf_reader_and_api_fallback_are_existing_logic(self):
        self.dashboard.AdvocateWeb.return_value.extract_day_cases_pdf_text.return_value = 'synthetic PDF'
        with patch.object(self.dashboard, 'parse_day_cases_pdf_text', return_value=GROUPS):
            groups, source = self.dashboard.fetch_advocate_diaries_cause_groups(date(2026, 10, 23))
        self.assertEqual(source, 'PDF')
        self.dashboard.AdvocateDiaries.assert_not_called()
        self.dashboard.AdvocateWeb.return_value.extract_day_cases_pdf_text.side_effect = RuntimeError('PDF unavailable')
        self.dashboard.AdvocateDiaries.return_value.daily_cause_list.return_value = {
            'success': True, 'data': {'groups': GROUPS}}
        groups, source = self.dashboard.fetch_advocate_diaries_cause_groups(date(2026, 10, 23))
        self.assertEqual(source, 'API')
        self.assertEqual(groups[0]['cases'][0]['case_number'], 'TEST/1/2026')


if __name__ == '__main__':
    unittest.main()
