"""Isolated regression checks without backend or spreadsheet access."""
import ast
import re
import unittest
from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo


def load_functions():
    source = Path(__file__).resolve().parents[1] / 'tools/memo_system/change_order.py'
    nodes = [n for n in ast.parse(source.read_text()).body
             if isinstance(n, ast.FunctionDef) or
             (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and
              t.id.startswith(('STATUS_', 'FIELD_', 'SYNC_STATUSES')) for t in n.targets))]
    module = ast.Module(body=ast.parse('from __future__ import annotations').body + nodes, type_ignores=[])
    scope = dict(re=re, datetime=datetime, date=date, timedelta=timedelta, ZoneInfo=ZoneInfo)
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), scope)
    return scope


class ChangeOrderDatesTest(unittest.TestCase):
    def test_scan_form_and_completion_preserve_dates(self):
        ns = load_functions()
        statuses = ['待收款', '待加收', '待扣儲值金', '已收款', '已加收', '已扣儲值金',
                    '待退款', '待返儲值金', '已退款', '已返儲值金']
        for status in statuses:
            with self.subTest(status=status):
                raw = [''] * 31
                for index, value in [(1, status), (6, 'TEST'), (12, '2026-09-20'),
                                     (28, '2026-09-25'), (13, '100'), (18, '100')]:
                    raw[index] = value
                ws = Mock()
                ws.get_all_values.return_value = [['header'], raw]
                ns['get_worksheet'] = lambda region: ws
                items = ns['get_pending_rows']('test')
                self.assertEqual(len(items), 1)
                form = {}
                ns['apply_sheet_row_to_form'](form, {}, items[0])
                charge = status in statuses[:6]
                self.assertEqual(form['chargeDate' if charge else 'refundDate'],
                                 '2026-09-20' if charge else '2026-09-25')
                ns['mark_sheet_row_done']('test', 2, items[0]['kind'])
                self.assertEqual([c.args[0] for c in ws.update_acell.call_args_list], ['AD2'])

    def test_processed_invoice_only_prepends_finance_note(self):
        ns = load_functions()
        raw = [''] * 31
        raw[1] = '已處理發票'
        raw[6] = 'LC001'
        raw[10] = '原發票號碼：AA12345678\n新發票號碼：BB12345678'
        raw[14] = 'BB12345678'
        ws = Mock()
        ws.get_all_values.return_value = [['header'], raw]
        ns['get_worksheet'] = lambda region: ws

        items = ns['get_pending_rows']('test')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'invoice')
        form = {'memoFinance': '原本財務備註', 'serviceNote': '原本客服備註'}
        controls = {
            'memoFinance': [{'type': 'textarea', 'context': '財務備註'}],
            'serviceNote': [{'type': 'textarea', 'context': '客服備註'}],
        }
        original_keys = set(form)

        ns['apply_sheet_row_to_form'](form, controls, items[0])

        self.assertEqual(set(form), original_keys)
        self.assertEqual(form['serviceNote'], '原本客服備註')
        self.assertEqual(
            form['memoFinance'],
            '原發票號碼：AA12345678\n新發票號碼：BB12345678\n原本財務備註',
        )


if __name__ == '__main__':
    unittest.main()
