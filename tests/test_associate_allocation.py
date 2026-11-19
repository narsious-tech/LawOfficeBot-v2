"""Pure selection/render tests without connecting to office services."""
import ast
import hashlib
import json
import unittest
from datetime import date
from pathlib import Path

source = ast.parse(Path('commands/associate_allocation.py').read_text())
functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {'case_key','chunks','report','board'}]
ns = {'hashlib':hashlib,'json':json, 'Button':lambda text, **kw: (text,kw), 'Markup':lambda rows: rows}
exec(compile(ast.Module(body=functions, type_ignores=[]), '<allocation>', 'exec'),ns)

class SelectionTests(unittest.TestCase):
    def row(self):
        return dict(case_number='CS/1/2026',case_title='A v B',court='Civil',judge='Judge A',floor='1',room='2',purpose='Reply')
    def test_same_number_in_other_court_is_not_merged(self):
        a=self.row();b=dict(a,court='Other court')
        self.assertNotEqual(ns['case_key'](a),ns['case_key'](b))
    def test_identity_ignores_case_and_whitespace(self):
        a=self.row();b=dict(a,case_number=' cs/1/2026 ')
        self.assertEqual(ns['case_key'](a),ns['case_key'](b))
    def test_preview_contains_case_and_court_details(self):
        text=ns['report'](date(2026,11,20),[self.row()])
        for value in ['A v B','Judge A','CS/1/2026','Reply','20-11-2026']:
            self.assertIn(value,text)
    def test_pagination_and_selected_marker(self):
        state={'id':'abc','date':date(2026,11,20),'cases':[self.row() for _ in range(8)],'selected':{6}}
        text,buttons=ns['board'](state,1)
        self.assertIn('✅ 7.',text)
        self.assertIn('Page 2/2',text)
        self.assertEqual(buttons[1][0][1]['callback_data'],'ia:abc:t:6:1')
    def test_chunks_never_exceed_telegram_limit(self):
        text=('case details\n'*1000)
        chunks=list(ns['chunks'](text))
        self.assertTrue(all(len(c)<=3500 for c in chunks))
        self.assertIn('case details',chunks[-1])
if __name__=='__main__': unittest.main()
