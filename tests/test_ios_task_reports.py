"""Offline API, deadline classification, source-failure and read-only tests."""
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
ROUTES = ('office-tasks', 'overdue-tasks', 'due-today-tasks', 'task-deadlines')


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    return result


def task(task_id=1, **values):
    return {'id': task_id, 'task': 'Prepare synthetic brief', 'status': 'PENDING',
            'assigned_to': 'Example Staff', 'case_number': 'TEST/1/2026',
            'priority': 'NORMAL', 'due_at': None, 'deadline': None,
            'source_type': 'manual', 'source_work_id': None, **values}


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'IOS_OFFICE_DASHBOARD_ENABLED': 'true',
            'IOS_OFFICE_DASHBOARD_TOKEN': TOKEN, 'ADMIN_USER_ID': '12345'}, clear=True)
        self.env.start()
        self.profile = Mock(return_value={'is_active': True})
        self.builder = Mock(return_value={'ok': True, 'summary': 'TASKS'})
        self.modules = patch.dict(sys.modules, {
            'services.role_intelligence_service': module('role', staff_profile=self.profile),
            'services.ios_task_reports': module('tasks', build_task_report=self.builder)})
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

    def test_routes_admin_binding_and_cache_protection(self):
        for path, kind in zip(ROUTES, ('office_tasks', 'overdue', 'due_today', 'deadlines')):
            r = self.get(path, headers=self.auth)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json['summary'], 'TASKS')
            self.assertEqual(r.headers['Cache-Control'], 'no-store, private')
            self.assertEqual(r.headers['Vary'], 'Authorization')
            self.assertNotIn('Access-Control-Allow-Origin', r.headers)
            self.builder.assert_called_with(kind)
            self.profile.assert_called_with(12345, read_only=True)

    def test_auth_and_configuration_before_data_access(self):
        for path in ROUTES:
            for value in ('', 'Bearer wrong', 'Basic a', 'Bearer café'):
                self.assertEqual(self.get(path, headers={'Authorization': value}).status_code, 401)
            for key, value in [('IOS_OFFICE_DASHBOARD_ENABLED', 'false'),
                               ('IOS_OFFICE_DASHBOARD_TOKEN', ''), ('ADMIN_USER_ID', '-1')]:
                with patch.dict(os.environ, {key: value}):
                    self.assertEqual(self.get(path, headers=self.auth).status_code, 503)
        self.profile.assert_not_called()
        self.builder.assert_not_called()

    def test_methods_and_query_overrides_blocked(self):
        for path in ROUTES:
            for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
                r = self.client.open('/api/ios/' + path, method=method, headers=self.auth)
                self.assertEqual(r.status_code, 405)
                self.assertEqual(r.headers['Allow'], 'GET')
            for query in ('?user_id=999', '?date=2020-01-01', '?token=' + TOKEN):
                self.assertEqual(self.get(path + query, headers=self.auth).status_code, 400)
        self.builder.assert_not_called()
        self.profile.assert_not_called()

    def test_inactive_and_source_unavailable(self):
        self.profile.return_value = {'is_active': False}
        self.assertEqual(self.get('office-tasks', headers=self.auth).status_code, 403)
        self.builder.assert_not_called()
        self.profile.return_value = {'is_active': True}
        self.builder.return_value = {'ok': False, 'summary': 'Source unavailable'}
        r = self.get('task-deadlines', headers=self.auth)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json['summary'], 'Source unavailable')

    def test_exception_details_not_returned_or_logged(self):
        self.builder.side_effect = RuntimeError('SECRET_DATABASE_PASSWORD')
        with self.assertLogs(self.app.logger, level='ERROR') as captured:
            r = self.get('office-tasks', headers=self.auth)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn('SECRET', r.text + ''.join(captured.output))


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, {})
        self.modules.start()
        spec = importlib.util.spec_from_file_location('actual_task_reports', ROOT / 'services/ios_task_reports.py')
        self.reports = importlib.util.module_from_spec(spec)
        with patch('zoneinfo.ZoneInfo', return_value=timezone(timedelta(hours=5, minutes=30))):
            spec.loader.exec_module(self.reports)
        frozen = type('Frozen', (datetime,), {'now': classmethod(
            lambda cls, tz=None: datetime(2026, 10, 23, 14, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(tz))})
        self.clock = patch.object(self.reports, 'datetime', frozen)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.modules.stop()

    def report(self, kind, rows, records=()):
        with patch.object(self.reports, '_read_local_tasks', return_value=rows), \
             patch.object(self.reports, '_read_live_works', return_value=list(records)):
            return self.reports.build_task_report(kind)

    def test_calendar_day_buckets_and_unknown_dates(self):
        rows = [task(1, deadline='22-10-2026'), task(2, deadline='23-10-2026 8:00 AM'),
                task(3, due_at='2026-10-24T00:00:00'), task(4), task(5, deadline='bad')]
        report = self.report('deadlines', rows)
        self.assertEqual(report['local_counts'], {'pending': 5, 'overdue': 1, 'due_today': 1, 'future': 1, 'undated': 2})
        self.assertIn('Task #1', report['summary'])
        self.assertIn('Task #2', report['summary'])
        self.assertNotIn('Task #3', report['summary'])
        self.assertIn('No usable deadline: 2', report['summary'])

    def test_aware_dates_convert_to_ist_and_due_at_precedes_deadline(self):
        rows = [task(1, due_at='2026-10-22T20:00:00+00:00', deadline='01-01-2020'),
                task(2, due_at='bad', deadline='23/10/2026'), task(3, deadline=date(2026, 10, 22))]
        report = self.report('deadlines', rows)
        self.assertEqual(report['local_counts']['due_today'], 2)
        self.assertEqual(report['local_counts']['overdue'], 1)

    def test_datetime_values_and_naive_ist(self):
        # Test real datetime objects separately from the patched wall clock.
        with patch.object(self.reports, 'datetime', datetime):
            self.assertEqual(self.reports._deadline(datetime(2026, 10, 23, 8)).hour, 8)
            value = self.reports._deadline(datetime(2026, 10, 22, 20, tzinfo=timezone.utc))
            self.assertEqual(value.date(), date(2026, 10, 23))
            self.assertEqual(value.hour, 1)

    def test_separate_overdue_and_due_today_filters(self):
        rows = [task(1, deadline='22-10-2026'), task(2, deadline='23-10-2026')]
        overdue = self.report('overdue', rows)['summary']
        due = self.report('due_today', rows)['summary']
        self.assertIn('Task #1', overdue)
        self.assertNotIn('Task #2', overdue)
        self.assertIn('Task #2', due)
        self.assertNotIn('Task #1', due)

    def test_live_records_are_separate_and_never_hearing_deadlines(self):
        records = [types.SimpleNamespace(work_id='77', client='Client', case_number='TEST/1/2026',
            case_title='Synthetic', description='Prepare evidence', next_hearing='01-01-2020')]
        rows = [task(1, source_type='advocate_diaries_work', source_work_id='77', deadline='24-10-2026')]
        report = self.report('office_tasks', rows, records)
        self.assertEqual(report['local_counts']['pending'], 1)
        self.assertEqual(report['local_counts']['overdue'], 0)
        self.assertEqual(report['live_works_count'], 1)
        self.assertIn('do not add the counts', report['summary'])
        self.assertIn('Linked local task(s): #1', report['summary'])
        self.assertNotIn('01-01-2020', report['summary'])

    def test_no_false_linking_by_case_or_description(self):
        record = types.SimpleNamespace(work_id='88', client='Client', case_number='TEST/1/2026',
            case_title='Synthetic', description='Prepare synthetic brief')
        report = self.report('office_tasks', [task(source_type='advocate_diaries_work', source_work_id='77')], [record])
        self.assertIn('No matching pending local task', report['summary'])

    def test_live_failure_preserves_local_counts(self):
        with patch.object(self.reports, '_read_local_tasks', return_value=[task()]), \
             patch.object(self.reports, '_read_live_works', side_effect=RuntimeError('PRIVATE')):
            report = self.reports.build_task_report('office_tasks')
        self.assertTrue(report['ok'])
        self.assertTrue(report['partial'])
        self.assertIsNone(report['live_works_count'])
        self.assertEqual(report['local_counts']['pending'], 1)
        self.assertNotIn('PRIVATE', report['summary'])

    def test_local_failure_preserves_live_source_without_false_zero(self):
        with patch.object(self.reports, '_read_local_tasks', side_effect=RuntimeError('PRIVATE')), \
             patch.object(self.reports, '_read_live_works', return_value=[]):
            report = self.reports.build_task_report('office_tasks')
        self.assertTrue(report['ok'])
        self.assertTrue(report['partial'])
        self.assertIsNone(report['local_counts'])
        self.assertIn('Unavailable; not counted as zero', report['summary'])

    def test_deadlines_do_not_fetch_live_and_fail_if_local_unavailable(self):
        with patch.object(self.reports, '_read_local_tasks', side_effect=RuntimeError('PRIVATE')), \
             patch.object(self.reports, '_read_live_works') as live:
            report = self.reports.build_task_report('deadlines')
        self.assertFalse(report['ok'])
        self.assertIsNone(report['local_counts'])
        live.assert_not_called()

    def test_empty_sources_and_full_output(self):
        report = self.report('office_tasks', [])
        self.assertEqual(report['local_counts']['pending'], 0)
        self.assertEqual(report['live_works_count'], 0)
        report = self.report('office_tasks', [task(i) for i in range(300)])
        self.assertEqual(report['local_counts']['pending'], 300)
        self.assertIn('Task #299', report['summary'])

    def test_reader_select_only_optional_schema_and_close(self):
        con = MagicMock()
        cur = con.cursor.return_value.__enter__.return_value
        columns = [{'column_name': x} for x in ('id', 'task', 'status', 'deadline')]
        cur.fetchall.side_effect = [columns, [task()]]
        driver = module('psycopg2', connect=Mock(return_value=con))
        with patch.dict(sys.modules, {'psycopg2': driver,
             'psycopg2.extras': module('extras', RealDictCursor=object)}), \
             patch.dict(os.environ, {'DATABASE_URL': 'synthetic-only'}):
            rows = self.reports._read_local_tasks()
        self.assertEqual(len(rows), 1)
        con.set_session.assert_called_once_with(readonly=True)
        con.close.assert_called_once_with()
        con.commit.assert_not_called()
        for call in cur.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().startswith('SELECT'))
        query = cur.execute.call_args.args[0]
        self.assertIn('NULL AS "due_at"', query)
        self.assertIn("='PENDING'", query)

    def test_missing_schema_is_not_an_empty_list_and_closes(self):
        con = MagicMock()
        con.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        driver = module('psycopg2', connect=Mock(return_value=con))
        with patch.dict(sys.modules, {'psycopg2': driver, 'psycopg2.extras': module('extras', RealDictCursor=object)}), \
             patch.dict(os.environ, {'DATABASE_URL': 'synthetic-only'}):
            with self.assertRaises(RuntimeError):
                self.reports._read_local_tasks()
        con.close.assert_called_once_with()

    def test_live_reader_uses_pending_read_and_canonical_parser(self):
        response = types.SimpleNamespace(status_code=200, url='https://example.test/works?status=pending',
            headers={'Content-Type': 'text/html'}, text='<html>synthetic</html>')
        web = Mock()
        web.works.return_value = response
        web._is_login_response.return_value = False
        parser = Mock(return_value=['synthetic-record'])
        with patch.dict(sys.modules, {'advocate_web': module('web', AdvocateWeb=Mock(return_value=web)),
             'services.ad_work_service': module('ad', parse_works_html=parser)}):
            self.assertEqual(self.reports._read_live_works(), ['synthetic-record'])
            web.works.assert_called_once_with('pending')
            parser.assert_called_once_with(response.text)
            web.complete_work.assert_not_called()
            response.url = 'https://example.test/error'
            with self.assertRaises(RuntimeError):
                self.reports._read_live_works()
            response.url = 'https://example.test/works'
            web._is_login_response.return_value = True
            with self.assertRaises(RuntimeError):
                self.reports._read_live_works()


if __name__ == '__main__':
    unittest.main()
