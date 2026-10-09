import sys
import types
import unittest
from unittest.mock import patch
config = sys.modules.setdefault('config', types.ModuleType('config'))
config.DATABASE_URL = 'unused'
psycopg2 = sys.modules.setdefault('psycopg2', types.ModuleType('psycopg2'))
psycopg2.connect = unittest.mock.Mock()
extras = sys.modules.setdefault('psycopg2.extras', types.ModuleType('psycopg2.extras'))
extras.RealDictCursor = object
from services import staff_notification_delivery as delivery

class RecipientTests(unittest.TestCase):
    def test_exact_active_recipient_selection(self):
        rows = [{'staff_name': 'Priya'}, {'staff_name': 'Preet'}, {'staff_name': 'Preeti'}, {'staff_name': 'Samar'}]
        conn = unittest.mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        with patch.object(delivery.psycopg2, 'connect', return_value=conn):
            self.assertEqual([r['staff_name'] for r in delivery.staff_recipients(('Priya', 'Preet'))], ['Priya', 'Preet'])
            self.assertEqual(len(delivery.staff_recipients()), 4)
        self.assertIn('is_active', conn.cursor.return_value.__enter__.return_value.execute.call_args[0][0])

    def test_closed_window_keeps_queue_and_sends_nothing(self):
        conn = unittest.mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        cloud = types.ModuleType('services.whatsapp_cloud')
        cloud.normalize_phone = lambda p: p
        cloud.send_text_message = unittest.mock.Mock()
        with patch.dict(sys.modules, {'services.whatsapp_cloud': cloud}), patch.object(delivery.psycopg2, 'connect', return_value=conn):
            self.assertEqual(delivery.flush_staff_notifications('919999999999'), 'QUEUED')
        cloud.send_text_message.assert_not_called()

    def test_file_event_changes_with_selection_and_date(self):
        self.assertNotEqual(delivery.file_event_key('2026-10-10', 'case A'), delivery.file_event_key('2026-10-10', 'case B'))
        self.assertNotEqual(delivery.file_event_key('2026-10-10', 'case A'), delivery.file_event_key('2026-10-11', 'case A'))

if __name__ == '__main__':
    unittest.main()
