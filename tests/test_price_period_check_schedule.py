"""金額期間檢查：排程設定每日 17:00 派送到 order-system。"""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

from tools.scheduling import calendar_record as cr
from tools.scheduling import schedule_settings as ss

spec = importlib.util.spec_from_file_location('cloud_schedule', Path(__file__).parents[1] / 'scripts/cloud_schedule.py')
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class PricePeriodCheckScheduleTests(unittest.TestCase):
    def jobs(self, value):
        when = dt.datetime.fromisoformat(value).replace(tzinfo=cs.TZ)
        return {k: (w, i) for w, i, k in cs.plan(when, rules=ss.default_rules(), is_workday=lambda d: d.weekday() < 5)}

    def test_dispatched_daily_1700_to_order_system(self):
        for day in ['2026-11-27', '2026-11-28', '2026-11-29']:  # 平日、週六、週日都跑
            self.assertEqual(self.jobs(day + 'T17:00').get('price_period_check'),
                             ('jenny-smart/order-system:price-period-check-schedule.yml', {}))
            self.assertNotIn('price_period_check', self.jobs(day + 'T16:50'))
            self.assertNotIn('price_period_check', self.jobs(day + 'T17:10'))
        self.assertEqual(ss.system_name('price_period_check'), 'order-system')

    def test_self_recorded(self):
        self.assertIn('price_period_check', cr.SELF_RECORDED_KEYS)


if __name__ == '__main__':
    unittest.main()
