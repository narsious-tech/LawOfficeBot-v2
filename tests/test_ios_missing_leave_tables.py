"""Regression tests for the deployed report_build:UndefinedTable:42P01 failure."""
import unittest
from unittest.mock import patch

import test_ios_court_reports as fixtures


class UndefinedTable(Exception):
    pgcode = '42P01'


class MissingLeaveTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BuilderTests()
        self.fixture.setUp()
        self.fetch = patch.object(self.fixture.dashboard, 'fetch_advocate_diaries_cause_groups',
                                  return_value=(fixtures.GROUPS, 'PDF'))
        self.fetch.start()

    def tearDown(self):
        self.fetch.stop()
        self.fixture.tearDown()

    def test_missing_leave_table_shows_unfiltered_list_and_warning(self):
        with patch.object(self.fixture.leave, 'confirmed_leave_entries',
                          side_effect=UndefinedTable('PRIVATE SQL DETAILS')):
            report = self.fixture.reports.build_court_report('today')
        self.assertTrue(report['ok'])
        self.assertFalse(report['judge_leave_checked'])
        self.assertIsNone(report['excluded_hearings'])
        self.assertEqual(report['hearings'], 1)
        self.assertIn('TEST/1/2026', report['summary'])
        self.assertIn('NOT CHECKED', report['summary'])
        self.assertTrue(report['warnings'])
        self.assertNotIn('PRIVATE', report['summary'])

    def test_evening_missing_tables_also_recovers(self):
        with patch.object(self.fixture.leave, 'confirmed_leave_entries', side_effect=UndefinedTable()):
            report = self.fixture.reports.build_court_report('evening')
        self.assertEqual(report['court_date'], '2026-10-26')
        self.assertIn('TEST/1/2026', report['summary'])
        self.assertFalse(report['judge_leave_checked'])

    def test_empty_unfiltered_list_does_not_claim_leave_checked(self):
        self.fixture.dashboard.fetch_advocate_diaries_cause_groups.return_value = ([], 'API')
        with patch.object(self.fixture.leave, 'confirmed_leave_entries', side_effect=UndefinedTable()):
            report = self.fixture.reports.build_court_report('today')
        self.assertEqual(report['hearings'], 0)
        self.assertIsNone(report['excluded_hearings'])
        self.assertIn('Judge leave was not checked', report['summary'])

    def test_existing_leave_tables_still_apply_exclusions(self):
        with patch.object(self.fixture.leave, 'confirmed_leave_entries',
                          return_value=[{'judge_key': 'example judge'}]):
            report = self.fixture.reports.build_court_report('today')
        self.assertTrue(report['judge_leave_checked'])
        self.assertEqual(report['excluded_hearings'], 1)
        self.assertEqual(report['hearings'], 0)
        self.assertEqual(report['warnings'], [])

    def test_other_database_failures_remain_errors(self):
        for error in (RuntimeError('failure'), type('PermissionDenied', (Exception,), {'pgcode': '42501'})()):
            with patch.object(self.fixture.leave, 'confirmed_leave_entries', side_effect=error):
                with self.assertRaises(type(error)):
                    self.fixture.reports.build_court_report('today')

    def test_missing_table_outside_leave_read_is_not_suppressed(self):
        self.fixture.dashboard.fetch_advocate_diaries_cause_groups.side_effect = UndefinedTable()
        with self.assertRaises(UndefinedTable):
            self.fixture.reports.build_court_report('today')
