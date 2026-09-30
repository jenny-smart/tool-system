import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cloud_schedule', Path(__file__).parents[1] / 'scripts/cloud_schedule.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ScheduleTests(unittest.TestCase):
    def jobs(self, value):
        return m.plan(dt.datetime.fromisoformat(value).replace(tzinfo=m.TZ))

    def test_original_daily_times(self):
        for hm, task in [('01:00','schedule_report'),('01:10','staff_schedule'),('01:20','orders_report'),('01:30','staff_info'),('07:00','notify')]:
            self.assertIn(('scheduled_daily.yml', {'target':task}),self.jobs('2026-09-30T'+hm))
        self.assertEqual(self.jobs('2026-09-30T06:00'), [('scheduled_field.yml',{'target':'field_all'}),('scheduled_service.yml',{'target':'service_all'})])
        self.assertEqual(self.jobs('2026-09-30T06:01'), [])

    def test_month_end_and_leap_year(self):
        for date in ['2026-09-30','2026-02-28','2028-02-29','2026-12-31']:
            jobs=self.jobs(date+'T17:00')
            self.assertEqual(jobs,[('scheduled_monthly.yml',{'target':'half_month_orders_2','period':date[:7].replace('-','')+'-2'})])
            self.assertIn(('scheduled_daily.yml',{'target':'month_end_cleanup'}),self.jobs(date+'T22:00'))
        self.assertEqual(self.jobs('2028-02-28T17:00'),[])
        self.assertEqual(self.jobs('2026-09-29T22:00'),[])

    def test_month_start_prior_period(self):
        for hm, target in [('00:00','prepaid_report'),('00:30','stored_value_settlement'),('00:40','stored_value_prepaid')]:
            self.assertIn(('scheduled_monthly.yml',{'target':target,'period':'202612-2'}),self.jobs('2027-01-01T'+hm))
        self.assertEqual(self.jobs('2026-09-30T00:30'),[])

    def test_weekly_and_401(self):
        self.assertIn(('scheduled_fubon_pending_check.yml',{}),self.jobs('2026-09-29T10:00'))
        self.assertNotIn(('scheduled_fubon_pending_check.yml',{}),self.jobs('2026-09-30T10:00'))
        self.assertIn(('Scheduled_gmail_401.yml',{}),self.jobs('2026-10-20T00:00'))
        self.assertNotIn(('Scheduled_gmail_401.yml',{}),self.jobs('2026-10-21T00:00'))

    def test_five_cloud_expressions_cover_all_slots(self):
        slots={(h,0) for h in [0,1,6,7,8,10,12,14,16,17,18,22]} | {(1,10),(1,20),(0,30),(1,30),(0,40)}
        for day in range(1,32):
            for hour in range(24):
                for minute in [0,10,20,30,40,50]:
                    when=dt.datetime(2026,10,day,hour,minute,tzinfo=m.TZ)
                    if m.plan(when): self.assertIn((hour,minute),slots)

    def test_existing_run_is_not_dispatched_again(self):
        when=dt.datetime.now(m.TZ).replace(second=0,microsecond=0)
        slot=when.strftime('%Y%m%dT%H%M')
        with patch.object(m,'plan',return_value=[('scheduled_service.yml',{'target':'service_all'})]), patch.object(m,'api',return_value={'workflow_runs':[{'display_title':'cloud-'+slot}]}) as api, patch('sys.argv',['cloud_schedule','--timestamp',str(int(when.timestamp()))]):
            m.main()
            self.assertEqual(api.call_count,1)

    def test_stale_timestamp_is_rejected(self):
        with patch('sys.argv',['cloud_schedule','--timestamp','1']), patch.object(m,'api') as api:
            with self.assertRaises(SystemExit): m.main()
            api.assert_not_called()

if __name__=='__main__': unittest.main()
