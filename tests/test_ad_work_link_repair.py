import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from services.ad_work_service import AdvocateWork
from services.ad_work_link_repair import suggest_case_context, task_snapshot, apply_confirmed_context

class WorkLinkRepairTests(unittest.TestCase):
    def setUp(self):
        self.task={'id':61,'task':'Draft reply of POD','assigned_to':'Happy','case_number':None,'notes':None,'source_type':'advocate_diaries_work','source_work_id':None,'status':'PENDING'}
        self.work=AdvocateWork(None,'GURWINDER SINGH','Kuldeep Singh VS Shivansh Mohan','Civil SUIT','CS/5365/2025','22-10-2026','Draft reply of POD','')

    def test_orphan_gets_preview_candidate_without_fabricated_work_id(self):
        candidates=suggest_case_context(self.task,[self.work])
        self.assertEqual(candidates[0]['case_number'],'CS/5365/2025')
        self.assertIsNone(candidates[0]['work_id'])
        self.assertIsNone(self.task['case_number'])

    def test_repeated_description_offers_multiple_candidates_not_automatic_choice(self):
        other=AdvocateWork(None,'Other','Gamma vs Delta','CS','CS/999/2026','','Draft reply of POD','')
        self.assertEqual(len(suggest_case_context(self.task,[self.work,other])),2)

    def test_manual_tasks_and_conflicting_context_are_not_candidates(self):
        self.assertEqual(suggest_case_context({**self.task,'source_type':'manual'},[self.work]),[])
        self.assertEqual(suggest_case_context({**self.task,'case_number':'CS/999/2026'},[self.work]),[])
        self.assertEqual(suggest_case_context({**self.task,'source_work_id':'123'},[self.work]),[])

    def test_changed_source_is_rejected_before_connecting(self):
        with self.assertRaisesRegex(ValueError,'changed or disappeared'):
            candidate=suggest_case_context(self.task,[self.work])[0]
            db=types.ModuleType('psycopg2'); db.connect=MagicMock()
            extras=types.ModuleType('psycopg2.extras'); extras.RealDictCursor=object; extras.Json=lambda x:x
            cfg=types.ModuleType('config'); cfg.DATABASE_URL='unused'
            with patch.dict(sys.modules,{'psycopg2':db,'psycopg2.extras':extras,'config':cfg}):
                apply_confirmed_context(61,candidate,task_snapshot(self.task),7,[])
            db.connect.assert_not_called()

    def test_confirmed_repair_updates_existing_row_and_audits_owner(self):
        conn=MagicMock(); cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[dict(self.task),{'case_number':'CS/5365/2025','notes':'Kuldeep Singh VS Shivansh Mohan','source_work_id':None}]
        db=types.ModuleType('psycopg2'); db.connect=MagicMock(return_value=conn)
        extras=types.ModuleType('psycopg2.extras'); extras.RealDictCursor=object; extras.Json=lambda x:x
        cfg=types.ModuleType('config'); cfg.DATABASE_URL='unused'
        with patch.dict(sys.modules,{'psycopg2':db,'psycopg2.extras':extras,'config':cfg}):
            result=apply_confirmed_context(61,suggest_case_context(self.task,[self.work])[0],task_snapshot(self.task),7,[self.work])
        self.assertEqual(result['case_number'],'CS/5365/2025')
        self.assertIsNone(result['source_work_id'])
        calls=cur.execute.call_args_list
        update=next(c for c in calls if 'UPDATE tasks' in c.args[0])
        self.assertEqual(update.args[1][-1],61)
        for field in ('assigned_to=', 'status=', 'task=', 'deadline='):
            self.assertNotIn(field,update.args[0])
        self.assertFalse(any('INSERT INTO tasks' in c.args[0] for c in calls))
        audit=next(c for c in calls if 'INSERT INTO ad_work_context_repairs' in c.args[0])
        self.assertEqual(audit.args[1][:2],(61,7))
        conn.commit.assert_called_once()

    def test_repair_command_and_callbacks_are_owner_only(self):
        from services.access_policy import required_level_for_command, required_level_for_callback
        self.assertEqual(required_level_for_command('/repairworklinks'), 'admin')
        self.assertEqual(required_level_for_callback('awr:confirm:token'), 'admin')

    def test_handler_itself_rejects_other_staff_and_group_chats(self):
        import ast
        from pathlib import Path
        from types import SimpleNamespace
        function=next(n for n in ast.parse(Path('commands/ad_work_repair.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_allowed')
        ns={'configured_admin_ids':lambda:{7}}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<owner-gate>','exec'),ns)
        def update(uid,chat_type): return SimpleNamespace(effective_chat=SimpleNamespace(type=chat_type),effective_user=SimpleNamespace(id=uid))
        self.assertTrue(ns['_allowed'](update(7,'private')))
        self.assertFalse(ns['_allowed'](update(8,'private')))
        self.assertFalse(ns['_allowed'](update(7,'group')))

if __name__=='__main__': unittest.main()
