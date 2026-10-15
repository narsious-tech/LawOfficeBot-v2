import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from services.ad_work_service import parse_works_html, AdvocateWork
from services import ad_work_case_repair as repair


def page(details, work_id='600', description='Draft reply'):
    return f'<table><tbody><tr><td>Client A</td><td>{details}</td><td>{description}</td><td><a href="mark_as_complete?work={work_id}">Complete</a></td></tr></tbody></table>'

class WorkCaseDetailsTests(unittest.TestCase):
    def test_separate_label_spans_preserve_title_number_and_hearing(self):
        records=parse_works_html(page('<b>Case Title:</b><span>Alpha vs Beta</span><br><b>Case Type:</b>CS<br><b>Case Number:</b><span>CS/123/2026</span><br><b>Next Hearing:</b><span>20-10-2026</span>'))
        self.assertEqual(records[0].case_title, 'Alpha vs Beta')
        self.assertEqual(records[0].case_number, 'CS/123/2026')
        self.assertEqual(records[0].next_hearing, '20-10-2026')
        self.assertEqual(records[0].work_id, '600')

    def test_inline_and_case_no_alias(self):
        record=parse_works_html(page('case title: Alpha vs Beta<br>Case No.: CS/456/2026<br>Next Hearing: 21-10-2026'))[0]
        self.assertEqual(record.case_title, 'Alpha vs Beta')
        self.assertEqual(record.case_number, 'CS/456/2026')

    def test_blank_label_cannot_consume_next_label(self):
        record=parse_works_html(page('Case Title:<br>Case Type: CS<br>Case Number:<br>Next Hearing: 21-10-2026'))[0]
        self.assertEqual(record.case_title, '')
        self.assertEqual(record.case_number, '')
        self.assertEqual(record.case_type, 'CS')

    def test_same_description_with_different_work_ids_remains_distinct(self):
        html=page('Case Title: Alpha vs Beta', '600')+page('Case Title: Gamma vs Delta', '601')
        # Parser operates on one work table, as the source does.
        html=html.replace('</tbody></table><table><tbody>', '')
        self.assertEqual(len(parse_works_html(html)), 2)

    def test_repair_uses_work_id_only_and_preserves_assignment_fields(self):
        conn=MagicMock(); cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value=[('600',)]; cur.rowcount=1
        db=types.ModuleType('psycopg2'); db.connect=MagicMock(return_value=conn)
        cfg=types.ModuleType('config'); cfg.DATABASE_URL='unused'
        records=[AdvocateWork('600','Client','Alpha vs Beta','CS','CS/123/2026','','Draft reply',''), AdvocateWork('601','Other','Wrong case','CS','CS/999/2026','','Draft reply','')]
        with patch.dict(sys.modules,{'psycopg2':db,'config':cfg}):
            result=repair.repair_work_case_details(records)
        updates=[call for call in cur.execute.call_args_list if 'UPDATE tasks' in call.args[0]]
        self.assertEqual(result['updated'],1)
        self.assertEqual(len(updates),1)
        sql, params=updates[0].args
        self.assertEqual(params[:3],('CS/123/2026','Alpha vs Beta','600'))
        self.assertIn("source_type='advocate_diaries_work'",sql)
        self.assertIn('NULLIF(TRIM(notes)',sql)
        for field in ('assigned_to=', 'status=', 'task=', 'deadline='):
            self.assertNotIn(field,sql)

    def test_whatsapp_list_shows_both_title_and_number_and_guards_blank_join(self):
        import ast
        from pathlib import Path
        function = next(n for n in ast.parse(Path('services/whatsapp_staff_companion.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_my_work')
        ns={'CLOSED':('COMPLETED',)}
        exec(compile(ast.Module(body=[function],type_ignores=[]), '<mywork>', 'exec'),ns)
        cur=MagicMock(); cur.fetchone.return_value={'task_table':'tasks'}
        cur.fetchall.return_value=[{'task_id':60,'task_text':'Complete the file','case_title':'Alpha vs Beta','task_case_number':'CS/123/2026'}]
        with patch.object(repair,'repair_work_case_details'):
            message=ns['_my_work'](cur,'Happy')
        self.assertIn('Alpha vs Beta',message)
        self.assertIn('CS/123/2026',message)
        query=cur.execute.call_args[0][0]
        self.assertIn("NULLIF(TRIM(t.case_number),'') IS NOT NULL",query)
        self.assertIn('HAVING COUNT(DISTINCT',query)

if __name__=='__main__': unittest.main()
