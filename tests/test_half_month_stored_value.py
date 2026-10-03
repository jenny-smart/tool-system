"""以隔離環境測試月訂單的真實函數，不登入後台或寫入 Drive。"""
import ast
import calendar
import json
import os
import tempfile
import re
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlparse
import pandas as pd

SOURCE = Path(__file__).parents[1] / 'tools/scheduled_monthly/half_month_orders.py'


def functions():
    names = {'build_export_url', 'period_to_dates', 'purchase_records', '_customer_key', '_address_city', 'stored_value_customer_city', 'export_stored_value', 'process_city'}
    tree = ast.parse(SOURCE.read_text())
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes += [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    def request(method, url, params):
        return SimpleNamespace(prepare=lambda: SimpleNamespace(url=url + '?' + urlencode(params)))
    ns = dict(re=re, json=json, calendar=calendar, unicodedata=unicodedata, pd=pd, requests=SimpleNamespace(Request=request), EXPORT_URL='https://backend.lemonclean.com.tw/purchase/export_order', HEADERS={}, log=lambda *args: None)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), ns)
    return ns


class StoredValueTests(unittest.TestCase):
    def test_half_month_service_dates_not_payment_dates(self):
        ns = functions()
        for period, expected in [('202609-1', ('2026-09-01', '2026-09-15')), ('202609-2', ('2026-09-16', '2026-09-30'))]:
            start, end, _ = ns['period_to_dates'](period)
            self.assertEqual((start, end), expected)
            query = parse_qs(urlparse(ns['build_export_url'](start, end, stored_value=True)).query, keep_blank_values=True)
            self.assertEqual([query[k][0] for k in ['clean_date_s','clean_date_e','paid_at_s','paid_at_e','buy','purchase_status']], [start, end, '', '', '5', '1'])

    def test_search_paginates_and_rejects_login(self):
        ns = functions()
        session = Mock()
        def page(items, last):
            return SimpleNamespace(url='https://backend.lemonclean.com.tw/purchase', text='purchaseList: ' + json.dumps({'data':items, 'last_page':last}), raise_for_status=lambda: None)
        session.get.side_effect = [page([{'order_no':'LC1'}],2), page([{'order_no':'LC2'}],2)]
        self.assertEqual(len(ns['purchase_records'](session, name='客戶')), 2)
        self.assertEqual(session.get.call_args.kwargs['params'], {'name':'客戶','page':2})
        session.get.side_effect = None
        session.get.return_value = SimpleNamespace(url='https://backend.lemonclean.com.tw/login', text='', raise_for_status=lambda:None)
        with self.assertRaises(RuntimeError): ns['purchase_records'](session, name='客戶')

    def test_name_lookup_checks_identity_and_routes_addresses(self):
        ns = functions()
        order = {'order_no':'LC1','name':'客戶','member_id':12,'phone':'0912345678'}
        for address, city in [('新竹市東區測試路1號','新竹'), ('新竹縣竹北市測試路1號','新竹'), ('臺南市東區測試路1號','高雄'), ('高雄市鼓山區測試路1號','高雄'), ('台北市中山區測試路1號','其他')]:
            fetch = Mock(return_value=[dict(order, member_id=99, address='台北市測試路1號'),dict(order,address=address)])
            ns['purchase_records'] = fetch
            self.assertEqual(ns['stored_value_customer_city'](object(), order), city)
            self.assertEqual(fetch.call_args.kwargs, {'name':'客戶','p_board':'on'})
        for items in [[], [dict(order,address=None)], [dict(order,address='新竹市測試路1號'),dict(order,address='高雄市測試路1號')]]:
            ns['purchase_records'] = Mock(return_value=items)
            with self.assertRaises(RuntimeError): ns['stored_value_customer_city'](object(), order)

    def test_export_filters_keeps_columns_and_caches_customer(self):
        for city, expected in [('新竹',['LC1','LC3']),('高雄',['LC2'])]:
            ns = functions()
            records = [{'order_no':'LC1','name':'竹','member_id':1}, {'order_no':'LC2','name':'南','member_id':2}, {'order_no':'LC3','name':'竹','member_id':1}]
            ns['purchase_records'] = Mock(return_value=records)
            lookup = Mock(side_effect=lambda session, order: '新竹' if order['member_id']==1 else '高雄')
            ns['stored_value_customer_city'] = lookup
            df = pd.DataFrame({'訂單編號':['LC1','LC2','LC3'], '客戶姓名':['竹','南','竹'], '金額':[100,200,300]})
            ns['download_export'] = Mock(return_value=b'file')
            ns['read_excel'] = Mock(return_value=df)
            result = ns['export_stored_value'](object(), city, '2026-09-01', '2026-09-15')
            self.assertEqual(result['訂單編號'].tolist(), expected)
            self.assertEqual(result.columns.tolist(), df.columns.tolist())
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(ns['purchase_records'].call_args.kwargs, dict(clean_date_s='2026-09-01',clean_date_e='2026-09-15',buy='5',purchase_status='1',p_board='on'))
            ns['read_excel'].return_value = df.iloc[:0]
            with self.assertRaises(RuntimeError): ns['export_stored_value'](object(), city, '2026-09-01', '2026-09-15')

    def test_merge_only_special_regions_and_keep_filename(self):
        for city in ['新竹', '高雄', '台北']:
            ns = functions()
            ns.update(os=os, tempfile=tempfile, STORED_VALUE_MERGE_CITIES={'新竹','高雄'})
            ns['requests'].Session = Mock(return_value=object())
            ns['login'] = Mock()
            ns['resolve_area_folder'] = Mock(return_value='area-folder')
            ns['get_or_create_single_child_folder'] = Mock(return_value='period-folder')
            ns['export_original'] = Mock(return_value=pd.DataFrame({'訂單編號':['LC1']}))
            ns['export_stored_value'] = Mock(return_value=pd.DataFrame({'訂單編號':['LC2']}))
            ns['write_monthly_log'] = Mock()
            saved = {}
            ns['persist_file'] = lambda service, path, *args: saved.update({Path(path).name: pd.read_excel(path)})
            args = SimpleNamespace(folder_id='root')
            ns['process_city'](city,args,{city:{'email':'test','password':'test'}},object(),'2026-09-16','2026-09-30','202609-2')
            expected = ['LC1','LC2'] if city in {'新竹','高雄'} else ['LC1']
            self.assertEqual(saved[f'202609-2訂單-{city}.xlsx']['訂單編號'].tolist(), expected)
            self.assertEqual(ns['export_stored_value'].call_count, 1 if city in {'新竹','高雄'} else 0)

    def test_lookup_failure_does_not_upload_partial_files(self):
        ns = functions()
        ns.update(os=os, tempfile=tempfile, STORED_VALUE_MERGE_CITIES={'新竹','高雄'})
        ns['requests'].Session = Mock(return_value=object())
        for name in ['login','resolve_area_folder','get_or_create_single_child_folder','write_monthly_log']:
            ns[name] = Mock()
        ns['export_original'] = Mock(return_value=pd.DataFrame({'訂單編號':['LC1']}))
        ns['export_stored_value'] = Mock(side_effect=RuntimeError('地址無法唯一分區'))
        ns['persist_file'] = Mock()
        with self.assertRaises(RuntimeError):
            ns['process_city']('新竹',SimpleNamespace(folder_id='root'),{'新竹':{'email':'test','password':'test'}},object(),'2026-09-01','2026-09-15','202609-1')
        ns['persist_file'].assert_not_called()

    def test_empty_and_missing_export_order_column(self):
        ns = functions()
        ns['purchase_records'] = Mock(return_value=[])
        ns['download_export'] = Mock(return_value=b'file')
        ns['read_excel'] = Mock(return_value=pd.DataFrame(columns=['訂單編號']))
        self.assertTrue(ns['export_stored_value'](object(), '新竹', '2026-09-01', '2026-09-15').empty)
        ns['read_excel'].return_value = pd.DataFrame({'未知欄位':['LC1']})
        with self.assertRaises(RuntimeError): ns['export_stored_value'](object(), '新竹', '2026-09-01', '2026-09-15')

if __name__ == '__main__': unittest.main()
