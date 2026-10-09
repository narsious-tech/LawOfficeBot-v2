import os
import sys
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from services.ecourts_date_access import is_date_action, can_manage_ecourts_dates, date_snapshot

class DateDelegationTests(unittest.TestCase):
    def test_scope_excludes_admin_sync_and_general_approvals(self):
        self.assertTrue(is_date_action(command='/ecourtsdates'))
        self.assertTrue(is_date_action(callback='ecr:dateaccept:7'))
        for action in ('ecr:sync', 'ecr:approve:7', 'ecr:workapprove:7', 'ecr:home'):
            self.assertFalse(is_date_action(callback=action))
        self.assertFalse(is_date_action(command='/syncecourts'))

    def test_other_staff_id_cannot_use_delegation(self):
        with patch.dict(os.environ, {'ECOURTS_DATE_MANAGER_TELEGRAM_ID': '8413754577'}):
            self.assertFalse(can_manage_ecourts_dates(8939115098))
            self.assertFalse(can_manage_ecourts_dates(None))

    def test_snapshot_detects_changed_date_or_source(self):
        row = {'staff_next_date': '2026-11-13', 'ecourts_next_date': '2026-11-03', 'source_sync_run_id': 1}
        self.assertNotEqual(date_snapshot(row), date_snapshot({**row, 'ecourts_next_date': '2026-11-04'}))
        self.assertNotEqual(date_snapshot(row), date_snapshot({**row, 'source_sync_run_id': 2}))

    def test_inactive_account_denied(self):
        db = types.ModuleType('psycopg2')
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        db.connect = Mock(return_value=conn)
        cfg = types.ModuleType('config'); cfg.DATABASE_URL = 'unused'
        with patch.dict(sys.modules, {'psycopg2': db, 'config': cfg}):
            self.assertFalse(can_manage_ecourts_dates(8413754577))
        self.assertIn('is_active', conn.cursor.return_value.__enter__.return_value.execute.call_args[0][0])

    def _route_environment(self, active_actor=True):
        db = types.ModuleType('psycopg2')
        extras = types.ModuleType('psycopg2.extras'); extras.RealDictCursor = object; extras.Json = lambda x: x
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [{'telegram_user_id': 8413754577, 'staff_name': 'Priya', 'role': 'staff'}, None]
        db.connect = Mock(return_value=conn)
        cfg = types.ModuleType('config'); cfg.DATABASE_URL = 'unused'
        cloud = types.ModuleType('services.whatsapp_cloud'); cloud.normalize_phone = lambda x: x
        verification = types.ModuleType('services.ecourts_date_verification_service')
        verification.list_date_conflicts = Mock(); verification.reconcile_date_verifications = Mock(); verification.review_date_conflict = Mock()
        return conn, {'psycopg2': db, 'psycopg2.extras': extras, 'config': cfg, 'services.whatsapp_cloud': cloud, 'services.ecourts_date_verification_service': verification}, verification

    def test_expired_or_reused_confirmation_never_mutates(self):
        conn, modules, verification = self._route_environment()
        with patch.dict(sys.modules, modules):
            import importlib
            route = importlib.import_module('services.whatsapp_ecourts_dates')
            with patch.object(route, 'can_manage_ecourts_dates', return_value=True), patch.object(route.psycopg2, 'connect', return_value=conn):
                result = route.handle_date_inbound({'phone': '919999999999', 'action_id': 'ecd:confirm:expired'})
            self.assertIn('expired', result['reply'])
            verification.review_date_conflict.assert_not_called()
            query = conn.cursor.return_value.__enter__.return_value.execute.call_args[0][0]
            self.assertIn('actor_id=%s', query)
            self.assertIn('expires_at>NOW()', query)

    def test_unauthorised_whatsapp_cannot_mutate_even_forged_button(self):
        conn, modules, verification = self._route_environment()
        with patch.dict(sys.modules, modules):
            import services.whatsapp_ecourts_dates as route
            with patch.object(route, 'can_manage_ecourts_dates', return_value=False), patch.object(route.psycopg2, 'connect', return_value=conn):
                result = route.handle_date_inbound({'phone': '919999999999', 'action_id': 'ecd:confirm:forged'})
            self.assertIn('restricted', result['reply'])
            verification.review_date_conflict.assert_not_called()

if __name__ == '__main__': unittest.main()
