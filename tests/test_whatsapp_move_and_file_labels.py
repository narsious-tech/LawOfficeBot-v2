import ast
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, date
from unittest.mock import MagicMock, Mock, patch

# Reuse the project's isolated attendance fixture imports.
from tests import test_whatsapp_attendance_service as attendance_fixtures
from services import whatsapp_attendance_service as service
from tests.test_whatsapp_staff_companion import classify_staff_command, menu_rows
from services.whatsapp_dashboard_service import build_file_selection_picker

class MoveAndFileRegressionTests(unittest.TestCase):
    def test_move_aliases_and_slash_checkin_are_routed(self):
        for command in ('MOVE IN', 'movein', '/movein', 'Transfer', 'move office'):
            self.assertEqual(classify_staff_command(command), ('ATTENDANCE_BEGIN', 'MOVE'))
        self.assertEqual(classify_staff_command('/checkin'), ('ATTENDANCE_BEGIN', 'CHECKIN'))
        self.assertLessEqual(len(menu_rows()), 10)
        self.assertIn('Move In / Transfer', [row['title'] for row in menu_rows()])

    def test_closed_or_missing_session_cannot_transfer(self):
        for session in (None, {'status': 'CLOSED', 'checkout_time': datetime(2026,10,15,18)}):
            with patch.object(service, '_session', return_value=session):
                self.assertIsNotNone(service._action_error(Mock(), 1, 'MOVE', datetime(2026,10,15,19)))

    def _confirm(self, destination=2):
        now = datetime(2026,10,15,19,30)
        session = {'id': 7, 'status': 'OPEN', 'checkout_time': None,
                   'checkin_time': datetime(2026,10,15,10), 'checkin_office_id': 1,
                   'checkin_office_name': 'Court Chamber Office',
                   'current_office_id': 1, 'current_office_name': 'Court Chamber Office'}
        pending = {'action': 'MOVE', 'latitude': 30.9, 'longitude':75.8,
                   'office_id':destination,'office_name':'Evening Office','distance_meters':5}
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [pending, session, None, session]
        cur.rowcount = 1
        with patch.object(service.psycopg2, 'connect', return_value=conn, create=True), patch.object(service, 'ensure_whatsapp_attendance_schema'), patch.object(service, '_now_local', return_value=now), patch.object(service, '_nearest_office', return_value={'id':destination,'office_name':'Evening Office','distance_meters':5,'allowed_radius_meters':300}):
            result=service.confirm_attendance('919999999999', {'telegram_user_id':1, 'staff_name':'Samar'}, 'MOVE')
        return result, cur, conn

    def test_move_records_transfer_without_second_punch_or_reset(self):
        web = types.ModuleType('advocate_web'); web.AdvocateWeb = Mock(side_effect=AssertionError('MOVE must not punch AD'))
        with patch.dict(sys.modules, {'advocate_web':web}):
            result, cur, conn = self._confirm()
        self.assertTrue(result['success'])
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any('INSERT INTO attendance_movements' in q for q in queries))
        update = next(q for q in queries if 'UPDATE attendance_sessions' in q)
        self.assertNotIn('checkin_time=', update)
        self.assertNotIn("status='CLOSED'", update)
        self.assertIn('From: Court Chamber Office', result['group_notification'])
        self.assertIn('OFFICE TRANSFER', result['group_notification'])
        web.AdvocateWeb.assert_not_called()
        conn.commit.assert_called()

    def test_same_office_transfer_rolls_back(self):
        result, cur, conn = self._confirm(destination=1)
        self.assertFalse(result['success'])
        self.assertIn('already marked', result['reply'])
        self.assertFalse(any('INSERT INTO attendance_movements' in c.args[0] for c in cur.execute.call_args_list))
        conn.rollback.assert_called()

    def test_whatsapp_picker_exposes_party_name_and_case_number(self):
        _, _, _, picker=build_file_selection_picker([{'id':1,'case_number':'CS/5705/2026','case_title':'Beas Trading vs Surya Industries','selected':True}])
        self.assertIn('Beas Trading', picker[0]['title'])
        self.assertIn('CS/5705/2026', picker[0]['description'])

    def test_telegram_buttons_display_names_and_stable_case_callback(self):
        tree=ast.parse(Path('commands/evening_dashboard.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_selection_keyboard')
        class Button:
            def __init__(self,text,callback_data): self.text,self.callback_data=text,callback_data
        ns={'PAGE_SIZE':8,'InlineKeyboardButton':Button,'InlineKeyboardMarkup':lambda rows:rows}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<selection>','exec'), ns)
        rows=ns['_selection_keyboard']({'cases':[{'case_number':'CS/5705/2026','case_title':'Beas Trading vs Surya Industries'}],'selected':{0}}, date(2026,10,16))
        self.assertIn('Beas Trading', rows[0][0].text)
        self.assertIn('CS/5705/2026', rows[0][0].text)
        self.assertEqual(rows[0][0].callback_data, 'efs:2026-10-16:t:0:0')

if __name__=='__main__': unittest.main()
