"""Isolated regression tests: no credentials, database, Telegram, or HTTP traffic."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import timezone
from unittest.mock import Mock, patch

from flask import Flask
from services.ios_office_dashboard import register_ios_office_dashboard

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'a' * 43


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'IOS_OFFICE_DASHBOARD_ENABLED': 'true',
            'IOS_OFFICE_DASHBOARD_TOKEN': TOKEN,
            'ADMIN_USER_ID': '12345',
        }, clear=True)
        self.env.start()
        self.service = types.ModuleType('services.role_intelligence_service')
        self.service.role_dashboard = Mock(return_value={
            'profile': {'is_active': True, 'staff_name': 'PRIVATE'},
            'files': {'pending': 2}, 'works': {'pending': 3}, 'pending_updates': 4})
        self.modules = patch.dict(sys.modules, {'services.role_intelligence_service': self.service})
        self.modules.start()
        self.app = Flask(__name__)
        register_ios_office_dashboard(self.app)
        self.client = self.app.test_client()
        self.auth = {'Authorization': 'Bearer ' + TOKEN}

    def tearDown(self):
        self.modules.stop()
        self.env.stop()

    def test_success_and_no_private_profile(self):
        r = self.client.get('/api/ios/office-dashboard', headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['files']['pending'], 2)
        self.assertIn('Works pending: 3', r.json['summary'])
        self.assertNotIn('PRIVATE', r.text)
        self.assertIn('no-store', r.headers['Cache-Control'])
        self.assertEqual(r.headers['Vary'], 'Authorization')
        self.service.role_dashboard.assert_called_once_with(12345, read_only=True)

    def test_auth_before_service(self):
        for auth in ('', 'Bearer wrong', 'Basic abc', 'Bearer ' + TOKEN + 'x', 'Bearer café'):
            r = self.client.get('/api/ios/office-dashboard', headers={'Authorization': auth})
            self.assertEqual(r.status_code, 401)
        self.service.role_dashboard.assert_not_called()

    def test_fail_closed_configuration(self):
        for key, value in [('IOS_OFFICE_DASHBOARD_ENABLED', 'false'),
                           ('IOS_OFFICE_DASHBOARD_TOKEN', ''),
                           ('IOS_OFFICE_DASHBOARD_TOKEN', 'short'),
                           ('ADMIN_USER_ID', '-1'), ('ADMIN_USER_ID', '')]:
            with patch.dict(os.environ, {key: value}):
                self.assertEqual(self.client.get('/api/ios/office-dashboard', headers=self.auth).status_code, 503)
        self.service.role_dashboard.assert_not_called()

    def test_no_mutation_or_impersonation(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
            r = self.client.open('/api/ios/office-dashboard', method=method, headers=self.auth)
            self.assertEqual(r.status_code, 405)
            self.assertEqual(r.headers['Allow'], 'GET')
            self.assertIn('no-store', r.headers['Cache-Control'])
        for query in ('telegram_id=999', 'token=' + TOKEN):
            self.assertEqual(self.client.get('/api/ios/office-dashboard?' + query, headers=self.auth).status_code, 400)
        self.service.role_dashboard.assert_not_called()

    def test_inactive_and_safe_errors(self):
        self.service.role_dashboard.return_value['profile']['is_active'] = False
        self.assertEqual(self.client.get('/api/ios/office-dashboard', headers=self.auth).status_code, 403)
        self.service.role_dashboard.side_effect = RuntimeError('SECRET_DATABASE_URL')
        r = self.client.get('/api/ios/office-dashboard', headers=self.auth)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn('SECRET', r.text)

    def test_registration_in_existing_flask_app(self):
        tree = ast.parse((ROOT / 'attendance_app.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                 and n.module == 'services.ios_office_dashboard']
        nodes += [n for n in tree.body if isinstance(n, ast.Expr)
                  and isinstance(n.value, ast.Call)
                  and isinstance(n.value.func, ast.Name)
                  and n.value.func.id == 'register_ios_office_dashboard']
        self.assertEqual(len(nodes), 2)
        app = Flask('actual_registration')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<registration>', 'exec'),
             {'attendance_app': app})
        self.assertEqual(app.test_client().get('/api/ios/office-dashboard').status_code, 401)

    def test_full_flask_module_smoke(self):
        source = (ROOT / 'attendance_app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        stubs = {}
        for name in ('requests', 'psycopg2', 'config', 'advocate_web',
                     'services.whatsapp_cloud', 'services.staff_activity_service',
                     'services.whatsapp_staff_companion', 'services.whatsapp_owner_companion',
                     'services.whatsapp_client_reception'):
            stubs[name] = types.ModuleType(name)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in stubs:
                for alias in node.names:
                    setattr(stubs[node.module], alias.name, Mock())
        spec = importlib.util.spec_from_file_location('attendance_smoke', ROOT / 'attendance_app.py')
        module = importlib.util.module_from_spec(spec)
        # ZoneInfo data is deployment-provided; this smoke test avoids a local tzdata dependency.
        with patch.dict(sys.modules, stubs), patch('zoneinfo.ZoneInfo', return_value=timezone.utc):
            spec.loader.exec_module(module)
        client = module.attendance_app.test_client()
        self.assertEqual(client.get('/health').status_code, 200)
        self.assertEqual(client.get('/').status_code, 302)
        self.assertEqual(client.get('/api/ios/office-dashboard', headers=self.auth).status_code, 200)
        rules = {rule.rule for rule in module.attendance_app.url_map.iter_rules()}
        self.assertTrue({'/api/attendance', '/attendance-app', '/whatsapp/webhook'}.issubset(rules))

    def test_service_readonly_and_bot_compatibility(self):
        # Load the actual changed service with only the database driver stubbed.
        driver = types.ModuleType('psycopg2')
        driver.connect = Mock()
        extras = types.ModuleType('psycopg2.extras')
        extras.RealDictCursor = object
        with patch.dict(sys.modules, {'psycopg2': driver, 'psycopg2.extras': extras}):
            spec = importlib.util.spec_from_file_location('actual_role', ROOT / 'services/role_intelligence_service.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only'}):
            module._connect(read_only=True)
            driver.connect.return_value.set_session.assert_called_once_with(readonly=True)
        con = Mock()
        cur = Mock()
        con.__enter__ = Mock(return_value=con)
        con.__exit__ = Mock(return_value=False)
        cursor_context = Mock()
        cursor_context.__enter__ = Mock(return_value=cur)
        cursor_context.__exit__ = Mock(return_value=False)
        con.cursor.return_value = cursor_context
        cur.fetchone.side_effect = [{}, {}, {}, {}]
        pending = types.ModuleType('services.case_intelligence_service')
        pending.advocate_diaries_pending_cases = Mock(return_value=[])
        with patch.object(module, '_connect', return_value=con) as connect, \
             patch.object(module, 'ensure_schema') as schema, \
             patch.object(module, 'staff_profile', return_value={'staff_name':'Ajay','role':'admin'}), \
             patch.object(module, '_live_ad_pending_work_count', return_value=0), \
             patch.dict(sys.modules, {'services.case_intelligence_service': pending}):
            module.role_dashboard(12345, read_only=True)
            schema.assert_not_called()
            connect.assert_called_with(read_only=True)
            for call in cur.execute.call_args_list:
                self.assertTrue(call.args[0].lstrip().startswith('SELECT'))
            module.role_dashboard(12345)
            schema.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
