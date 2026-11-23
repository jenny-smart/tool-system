"""Isolated regression checks without backend or spreadsheet access."""
import ast
import re
import unittest
from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo


def load_functions():
    source = Path(__file__).resolve().parents[1] / 'tools/orders_system/memo_system/change_order.py'
    nodes = [n for n in ast.parse(source.read_text()).body
             if isinstance(n, ast.FunctionDef) or
             (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and
              t.id.startswith(('STATUS_', 'FIELD_', 'SYNC_STATUSES')) for t in n.targets))]
    module = ast.Module(body=ast.parse('from __future__ import annotations').body + nodes, type_ignores=[])
    scope = dict(re=re, datetime=datetime, date=date, timedelta=timedelta, ZoneInfo=ZoneInfo)
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), scope)
    return scope


class ChangeOrderDatesTest(unittest.TestCase):
    def test_blank_payment_flow_is_not_synced(self):
        """2026-11-24：R 欄空白時加收／退款金流不得空白，不回寫；儲值金類固定填儲值金。"""
        ns = load_functions()
        refund_controls = {'refundPayway': [{'type': 'select', 'context': '退款金流'}]}
        for status, label in [('已退款', '退款金流'), ('待退款', '退款金流'), ('已收款', '加收金流'), ('待收款', '加收金流')]:
            with self.subTest(status=status):
                raw = [''] * 31
                raw[1], raw[6] = status, 'LC001'
                with self.assertRaisesRegex(RuntimeError, label):
                    ns['apply_sheet_row_to_form']({}, refund_controls, {'raw': raw, 'status': status})
        raw = [''] * 31
        raw[1], raw[6], raw[17] = '已退款', 'LC001', '信用卡'
        with self.assertRaisesRegex(RuntimeError, '找不到後台「退款金流」欄位'):
            ns['apply_sheet_row_to_form']({}, {}, {'raw': raw, 'status': '已退款'})
        raw[1] = '已返儲值金'
        raw[17] = ''
        form = {}
        ns['apply_sheet_row_to_form'](form, refund_controls, {'raw': raw, 'status': '已返儲值金'})
        self.assertEqual(form['refundPayway'], '儲值金')

    def test_scan_form_and_completion_preserve_dates(self):
        ns = load_functions()
        statuses = ['待收款', '待加收', '待扣儲值金', '已收款', '已加收', '已扣儲值金',
                    '待退款', '待返儲值金', '已退款', '已返儲值金']
        for status in statuses:
            with self.subTest(status=status):
                raw = [''] * 31
                for index, value in [(1, status), (6, 'TEST'), (12, '2026-09-20'),
                                     (28, '2026-09-25'), (13, '100'), (17, '信用卡'), (18, '100')]:
                    raw[index] = value
                ws = Mock()
                ws.get_all_values.return_value = [['header'], raw]
                ns['get_worksheet'] = lambda region: ws
                items = ns['get_pending_rows']('test')
                self.assertEqual(len(items), 1)
                form = {}
                controls = {'refundPayway': [{'type': 'select', 'context': '退款金流'}]}
                ns['apply_sheet_row_to_form'](form, controls, items[0])
                charge = status in statuses[:6]
                self.assertEqual(form['chargeDate' if charge else 'refundDate'],
                                 '2026-09-20' if charge else '2026-09-25')
                ns['mark_sheet_row_done']('test', 2, status)
                ranges = [u['range'] for c in ws.batch_update.call_args_list for u in c.args[0]]
                self.assertIn('AD2', ranges)

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
        self.assertEqual(items[0]['kind'], 'finance_note')
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
