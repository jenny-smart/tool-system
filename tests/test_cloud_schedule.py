"""Cloud Schedule Router：依主控表「排程設定」（這裡用預設值）派送；各功能各自去重。"""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.scheduling import schedule_settings as ss

spec = importlib.util.spec_from_file_location('cloud_schedule', Path(__file__).parents[1] / 'scripts/cloud_schedule.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 測試用國定假日：2026-10-09（五）、2026-10-10（六，國慶）
HOLIDAYS = {dt.date(2026, 10, 9), dt.date(2026, 10, 10), dt.date(2027, 1, 1)}


def workday(day):
    return day.weekday() < 5 and day not in HOLIDAYS


class ScheduleTests(unittest.TestCase):
    def full(self, value, rules=None, invest=False):
        """invest=False 時排除 investment-system 的功能（每 10 分鐘都有，原本的完全比對才不受影響）；
        invest=True 時把預設不啟用的 investment-system 列當成已啟用來測時段。"""
        when = dt.datetime.fromisoformat(value).replace(tzinfo=m.TZ)
        rules = rules or ss.default_rules()
        if invest:
            for r in rules:
                if r.key.startswith('invest_'):
                    r.enabled = True
        jobs = m.plan(when, rules=rules, is_workday=workday)
        return jobs if invest else [j for j in jobs if not j[2].startswith('invest_')]

    def jobs(self, value, invest=False):
        return [(w, i) for w, i, _k in self.full(value, invest=invest)]

    def keys(self, value, invest=False):
        return [k for _w, _i, k in self.full(value, invest=invest)]

    def test_defaults_are_valid(self):
        rules = ss.default_rules()
        self.assertEqual([r.key for r in rules if r.errors], [])
        self.assertEqual({r.key for r in rules}, set(ss.FUNCTIONS))

    def test_original_daily_times(self):
        for hm, task in [('01:00','schedule_report'),('01:10','staff_schedule'),('01:20','orders_report'),('01:30','staff_info'),('07:00','notify')]:
            self.assertIn(('scheduled_daily.yml', {'target':task}),self.jobs('2026-09-30T'+hm))
        self.assertEqual(self.jobs('2026-09-30T05:00'), [('scheduled_field.yml',{'target':'field_all'})])
        self.assertEqual(self.jobs('2026-09-30T05:30'), [('scheduled_service.yml',{'target':'service_all'})])
        self.assertEqual(self.jobs('2026-09-30T06:00'), [])
        self.assertEqual(self.jobs('2026-09-30T06:01'), [])

    def test_month_end_and_leap_year(self):
        for date in ['2026-09-30','2026-02-28','2028-02-29','2026-12-31']:
            jobs=self.jobs(date+'T12:00')
            self.assertIn(('scheduled_monthly.yml',{'target':'half_month_orders_2','period':date[:7].replace('-','')+'-2'}),jobs)
            self.assertIn(('scheduled_daily.yml',{'target':'month_end_cleanup'}),self.jobs(date+'T22:00'))
        self.assertEqual(self.jobs('2028-02-28T12:00'),[('performance_report.yml',{})])
        self.assertIn(('scheduled_monthly.yml',{'target':'half_month_orders_1','period':'202611-1'}),self.jobs('2026-11-15T12:00'))
        self.assertEqual(self.jobs('2026-09-29T22:00'),[])

    def test_month_start_prior_period(self):
        for hm, target in [('00:00','prepaid_report'),('00:10','stored_value_settlement'),('00:20','stored_value_prepaid')]:
            self.assertIn(('scheduled_monthly.yml',{'target':target,'period':'202612-2'}),self.jobs('2027-01-01T'+hm))
        self.assertEqual(self.jobs('2027-01-01T00:40'),[])

    def test_month_start_finance_each_function_own_slot(self):
        for hm, key in [('01:00','vip_full_pipeline'),('03:00','revenue_reconcile'),('04:00','deposit_report'),
                        ('04:30','review_monthly'),('05:00','month_start_queue'),('06:00','fixed_expense'),
                        ('08:30','cash_gap_check')]:
            self.assertIn(key, self.keys('2026-11-01T'+hm))
            self.assertNotIn(key, self.keys('2026-11-02T'+hm))
        # 04:00 押金財報跟月初檢查同時段、同一個 workflow，兩個都派送
        self.assertTrue({'deposit_report', 'month_start_check'} <= set(self.keys('2026-11-01T04:00')))
        for hour in range(4, 24):
            self.assertIn('month_start_check', self.keys('2026-11-01T%02d:00' % hour))
        self.assertNotIn('month_start_check', self.keys('2026-11-01T03:00'))
        self.assertNotIn('month_start_check', self.keys('2026-11-02T09:00'))
        for day in range(2, 11):
            self.assertIn('cash_gap_followup', self.keys('2026-11-%02dT09:00' % day))
        self.assertNotIn('cash_gap_followup', self.keys('2026-11-11T09:00'))
        self.assertIn(('scheduled_finance.yml',{'target':'accounting_reminder'}),self.jobs('2026-11-10T10:00'))
        self.assertNotIn(('scheduled_finance.yml',{'target':'accounting_reminder'}),self.jobs('2026-12-10T10:00'))

    def test_fixed_expense_mail_and_transfer(self):
        # 5、8、10 日，遇例假日提前（2026-11-05 Jenny）：11/8 週日 → 11/6
        for day in (5, 6, 10):
            self.assertIn('fixed_expense_mail', self.keys('2026-11-%02dT09:00' % day))
        self.assertNotIn('fixed_expense_mail', self.keys('2026-11-08T09:00'))
        self.assertNotIn('fixed_expense_mail', self.keys('2026-11-09T09:00'))
        # 各區固定費用匯款：每月 10 日 04:00，遇例假日提前（2026/10/9、10/10 連假 → 10/8）
        self.assertIn('fixed_expense_remit', self.keys('2026-11-10T04:00'))
        self.assertIn('fixed_expense_remit', self.keys('2026-10-08T04:00'))
        self.assertNotIn('fixed_expense_remit', self.keys('2026-10-10T04:00'))
        self.assertNotIn('fixed_expense_remit', self.keys('2026-11-09T04:00'))

    def test_weekly_change_order_and_fubon(self):
        # 2026-11-03 週二、11-06 週五
        for day in ('2026-11-03', '2026-11-06'):
            self.assertIn(('scheduled_change_order_check.yml',{'target':'batch_scan'}), self.jobs(day+'T09:30'))
            self.assertIn(('scheduled_fubon_pending_check.yml',{}), self.jobs(day+'T10:30'))
        self.assertNotIn(('scheduled_fubon_pending_check.yml',{}), self.jobs('2026-11-03T10:00'))
        self.assertNotIn(('scheduled_change_order_check.yml',{'target':'batch_scan'}), self.jobs('2026-11-04T09:30'))
        self.assertIn(('scheduled_change_order_check.yml',{'target':'check'}), self.jobs('2026-11-04T10:00'))

    def test_mof_serial_earlier_on_holiday(self):
        # 雙月 20 日做下一期；單月不做
        self.assertNotIn('mof_serial', self.keys('2026-11-20T10:00'))
        self.assertIn('mof_serial', self.keys('2026-10-20T10:00'))
        # 2026-12-20 是週日 → 提前到 12-18（五）
        self.assertIn('mof_serial', self.keys('2026-12-18T10:00'))
        self.assertNotIn('mof_serial', self.keys('2026-12-20T10:00'))
        for day in range(21, 26):
            self.assertIn('mof_serial_check', self.keys('2026-12-%02dT10:00' % day))
            self.assertNotIn('mof_serial_check', self.keys('2026-11-%02dT10:00' % day))

    def test_yuanta_and_401(self):
        self.assertIn(('scheduled_yuanta_statement.yml',{}),self.jobs('2026-11-25T11:00'))
        # 2026-10-25 週日 → 提前到 10-23（五）
        self.assertIn(('scheduled_yuanta_statement.yml',{}),self.jobs('2026-10-23T11:00'))
        self.assertNotIn(('scheduled_yuanta_statement.yml',{}),self.jobs('2026-10-25T11:00'))
        self.assertIn(('Scheduled_gmail_401.yml',{}),self.jobs('2026-10-20T00:00'))
        self.assertNotIn(('Scheduled_gmail_401.yml',{}),self.jobs('2026-10-21T00:00'))

    def test_sheet_rows_override_and_validation(self):
        rows = [[True, "fixed_expense", "固定費用", "07:10", "每月", "1", "照跑", "直接執行", ""],
                [False, "review_monthly", "年度review", "04:30", "每月", "1", "照跑", "直接執行", ""],
                [True, "deposit_report", "押金", "04:05", "每月", "1", "照跑", "直接執行", ""],
                [True, "nope", "?", "04:00", "每日", "", "照跑", "直接執行", ""]]
        rules = [ss.parse_row(r) for r in rows]
        self.assertEqual(self.full('2026-11-01T07:10', rules), [('scheduled_finance.yml', {'target': 'fixed_expense'}, 'fixed_expense')])
        self.assertEqual(self.full('2026-11-01T04:30', rules), [])  # 停用
        self.assertTrue(rules[2].errors and rules[3].errors)  # 不是 10 分鐘倍數、不認得的代號
        self.assertEqual(self.full('2026-11-01T04:00', rules), [])

    def test_parse_time_range(self):
        times, errors = ss.parse_times("04:00~06:00 每小時, 08:30")
        self.assertEqual((times, errors), (["04:00", "05:00", "06:00", "08:30"], []))

    def test_existing_run_is_not_dispatched_again_per_function(self):
        now=dt.datetime.now(m.TZ)
        when=now.replace(minute=(now.minute//10)*10,second=0,microsecond=0)
        slot=when.strftime('%Y%m%dT%H%M')
        jobs=[('scheduled_finance.yml',{'target':'deposit_report'},'deposit_report'),
              ('scheduled_finance.yml',{'target':'month_start_check'},'month_start_check')]
        calls=[]
        def api(path, payload=None, repo='', token=''):
            calls.append((path, payload))
            return {'workflow_runs':[{'display_title':'cloud-'+slot+'-deposit_report'}]} if payload is None else {}
        with patch.object(m,'plan',return_value=jobs), patch.object(m,'api',side_effect=api), patch('sys.argv',['cloud_schedule','--timestamp',str(int(when.timestamp()))]):
            m.main()
        dispatched=[p for _path, p in calls if p is not None]
        self.assertEqual(dispatched, [{'ref':'main','inputs':{'target':'month_start_check','scheduled_slot':slot+'-month_start_check'}}])

    def _run_main(self, jobs, api, env=None):
        now=dt.datetime.now(m.TZ)
        when=now.replace(minute=(now.minute//10)*10,second=0,microsecond=0)
        with patch.object(m,'plan',return_value=jobs), patch.object(m,'api',side_effect=api), \
                patch.dict('os.environ', env or {}, clear=False), \
                patch('sys.argv',['cloud_schedule','--timestamp',str(int(when.timestamp()))]):
            m.main()
        return when.strftime('%Y%m%dT%H%M')

    def test_weekend_reminder_daily_1100_to_order_system(self):
        for day in ['2026-11-05', '2026-11-06', '2026-11-08']:
            self.assertIn(('jenny-smart/order-system:weekend-reminder-schedule.yml', {}), self.jobs(day+'T11:00'))
        self.assertNotIn('weekend_reminder', self.keys('2026-11-05T10:50'))
        self.assertEqual(ss.split_workflow('jenny-smart/order-system:weekend-reminder-schedule.yml'),
                         ('jenny-smart/order-system', 'weekend-reminder-schedule.yml'))
        self.assertEqual(ss.split_workflow('scheduled_daily.yml'), ('', 'scheduled_daily.yml'))

    def test_investment_system_rows(self):
        repo = 'jenny-smart/investment-system:'
        keys = lambda v: [k for k in self.keys(v, invest=True) if k.startswith('invest_')]
        # 2026-11-16 週一（月中，沒有月底固定）
        self.assertEqual(keys('2026-11-16T08:20'), ['invest_fund_nav'])
        self.assertEqual(sorted(keys('2026-11-16T08:30')),
                         ['invest_fund_nav', 'invest_keepalive', 'invest_tw_prices'])
        self.assertIn('invest_tw_prices', keys('2026-11-02T14:30'))
        self.assertNotIn('invest_tw_prices', keys('2026-11-02T14:40'))
        self.assertNotIn('invest_tw_prices', keys('2026-11-07T10:00'))       # 週六
        self.assertIn('invest_stock_radar', keys('2026-11-02T14:30'))
        # 美股：週一至五晚上、週二至六凌晨
        self.assertIn('invest_us_prices_night', keys('2026-11-06T23:50'))     # 週五
        self.assertNotIn('invest_us_prices_night', keys('2026-11-07T21:00'))  # 週六
        self.assertIn('invest_us_prices_morning', keys('2026-11-07T06:00'))   # 週六清晨
        self.assertNotIn('invest_us_prices_morning', keys('2026-11-07T06:10'))
        self.assertNotIn('invest_us_prices_morning', keys('2026-11-02T03:00'))  # 週一凌晨
        # 月底固定：1–10、28–31 日每 10 分鐘
        self.assertIn('invest_month_end_ledger', keys('2026-10-31T23:50'))
        self.assertIn('invest_month_end_ledger', keys('2026-11-10T00:10'))
        self.assertNotIn('invest_month_end_ledger', keys('2026-11-11T00:10'))
        self.assertIn('invest_snapshot', keys('2026-11-08T20:00'))
        self.assertIn((repo + 'tw-stock-prices.yml', {'mode': 'auto'}), self.jobs('2026-11-02T10:00', invest=True))
        self.assertIn((repo + 'month-end-ledger.yml', {'mode': 'auto'}), self.jobs('2026-11-01T00:00', invest=True))
        self.assertEqual(ss.EXTERNAL_DISPATCH_TOKENS['jenny-smart/investment-system'],
                         'INVESTMENT_SYSTEM_DISPATCH_TOKEN')

    def test_investment_rows_start_disabled(self):
        rows = [r for r in ss.DEFAULT_ROWS if r[1].startswith('invest_')]
        self.assertEqual(len(rows), 8)
        self.assertFalse(any(r[0] for r in rows))
        self.assertEqual([k for k in self.keys('2026-11-16T10:00', invest=False) if k.startswith('invest_')], [])

    def test_retired_investment_rows_do_not_run(self):
        row = [True, 'invest_fund_freeze', '舊', '00:30~23:30 每小時', '每月', '1-7', '照跑', ss.MODE_DIRECT, '']
        rule = ss.parse_row(row)
        self.assertTrue(rule.errors and '已停用' in rule.errors[0])
        self.assertEqual(m.plan(dt.datetime(2026, 11, 6, 1, 30, tzinfo=m.TZ), rules=[rule], is_workday=workday), [])

    def test_system_name(self):
        self.assertEqual(ss.system_name('notify'), 'tool-system')
        self.assertEqual(ss.system_name('weekend_reminder'), 'order-system')
        self.assertEqual(ss.system_name('invest_fund_nav'), 'investment-system')
        self.assertEqual(ss.system_name('nope'), '')
        self.assertEqual(ss.HEADER[9], '系統名稱')
        self.assertEqual(ss.with_system(ss.DEFAULT_ROWS[0])[9], 'tool-system')
        # 多一欄不影響解析
        self.assertEqual(ss.parse_row(ss.with_system(ss.DEFAULT_ROWS[0])).errors, [])

    def test_ensure_sheet_fills_system_column_without_touching_filled_cells(self):
        from unittest.mock import MagicMock
        service = MagicMock()
        values = service.spreadsheets.return_value.values.return_value
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            'sheets': [{'properties': {'title': ss.SHEET_NAME}}]}
        existing = [[r[1]] + r[2:7] for r in ss.DEFAULT_ROWS]
        existing.insert(2, [])  # 中間一列空白
        responses = {
            f"'{ss.SHEET_NAME}'!B2:G": {'values': existing},
            f"'{ss.SHEET_NAME}'!J1:J{len(existing) + 1}": {'values': [[], ['自訂名稱']]},
        }
        values.get.side_effect = lambda spreadsheetId, range: MagicMock(
            execute=MagicMock(return_value=responses[range]))
        logs = []
        self.assertFalse(ss.ensure_sheet(service, 'sid', log=logs.append))
        values.append.assert_not_called()
        data = values.batchUpdate.call_args.kwargs['body']['data']
        cells = {d['range']: d['values'][0][0] for d in data}
        self.assertEqual(cells[f"'{ss.SHEET_NAME}'!J1"], '系統名稱')
        self.assertNotIn(f"'{ss.SHEET_NAME}'!J2", cells)   # 已填「自訂名稱」不改
        self.assertNotIn(f"'{ss.SHEET_NAME}'!J4", cells)   # 空白列
        self.assertEqual(cells[f"'{ss.SHEET_NAME}'!J3"], 'tool-system')
        last = len(existing) + 1
        self.assertEqual(cells[f"'{ss.SHEET_NAME}'!J{last}"], 'investment-system')

    def test_cross_repo_dispatch_uses_repo_token(self):
        calls=[]
        def api(path, payload=None, repo='', token=''):
            calls.append((path, payload, repo, token))
            return {'workflow_runs':[]} if payload is None else {}
        jobs=[('jenny-smart/order-system:weekend-reminder-schedule.yml', {}, 'weekend_reminder'),
              ('scheduled_daily.yml', {'target':'notify'}, 'notify')]
        slot=self._run_main(jobs, api, {'ORDER_SYSTEM_DISPATCH_TOKEN':'tok'})
        sent=[(path, payload, repo, token) for path, payload, repo, token in calls if payload is not None]
        self.assertEqual(sent[0], ('/actions/workflows/weekend-reminder-schedule.yml/dispatches',
                                   {'ref':'main','inputs':{'scheduled_slot':slot+'-weekend_reminder'}},
                                   'jenny-smart/order-system', 'tok'))
        self.assertEqual(sent[1][2:], ('', ''))

    def test_missing_cross_repo_token_does_not_block_other_jobs(self):
        calls=[]
        def api(path, payload=None, repo='', token=''):
            calls.append((path, payload, repo))
            return {'workflow_runs':[]} if payload is None else {}
        jobs=[('jenny-smart/order-system:weekend-reminder-schedule.yml', {}, 'weekend_reminder'),
              ('scheduled_daily.yml', {'target':'notify'}, 'notify')]
        with patch.dict('os.environ', {'ORDER_SYSTEM_DISPATCH_TOKEN':''}), self.assertRaises(SystemExit):
            self._run_main(jobs, api)
        self.assertEqual([repo for _p, payload, repo in calls if payload is not None], [''])

    def test_cross_repo_failure_does_not_block_other_jobs(self):
        calls=[]
        def api(path, payload=None, repo='', token=''):
            if repo:
                raise RuntimeError('HTTP 403')
            calls.append(path)
            return {'workflow_runs':[]} if payload is None else {}
        jobs=[('jenny-smart/order-system:weekend-reminder-schedule.yml', {}, 'weekend_reminder'),
              ('scheduled_daily.yml', {'target':'notify'}, 'notify')]
        with self.assertRaises(SystemExit):
            self._run_main(jobs, api, {'ORDER_SYSTEM_DISPATCH_TOKEN':'tok'})
        self.assertTrue(any(path.endswith('/dispatches') for path in calls))

    def test_cloud_clock_skew(self):
        self.assertEqual(m.scheduled_time(1790755153).isoformat(), "2026-09-30T16:00:00+08:00")
        for text in ["2026-10-01T00:00", "2026-10-01T01:10", "2026-10-01T00:20", "2026-10-01T02:00"]:
            target=dt.datetime.fromisoformat(text).replace(tzinfo=m.TZ)
            for offset in [-120,-47,0,45,120]:
                self.assertEqual(m.scheduled_time(int(target.timestamp())+offset),target)
            with self.assertRaises(ValueError): m.scheduled_time(int(target.timestamp())+121)

    def test_delivery_window_and_same_day_catchup(self):
        slot=dt.datetime(2026,9,30,10,0,tzinfo=m.TZ)
        for minutes in [-30, -15, 0, 15, 30, 31, 90, 600]:
            m.validate_delivery(slot, slot+dt.timedelta(minutes=minutes))
        with self.assertRaises(SystemExit):
            m.validate_delivery(slot, slot-dt.timedelta(minutes=31))
        with self.assertRaises(SystemExit):
            m.validate_delivery(slot, slot+dt.timedelta(days=1))

    def test_stale_timestamp_is_rejected(self):
        with patch('sys.argv',['cloud_schedule','--timestamp','600']), patch.object(m,'api') as api:
            with self.assertRaises(SystemExit): m.main()
            api.assert_not_called()

if __name__=='__main__': unittest.main()
