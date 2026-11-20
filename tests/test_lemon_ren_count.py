"""檸檬人統計：排程設定每日 11:00 派送到 order-system。"""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

from tools.scheduling import schedule_settings as ss

spec = importlib.util.spec_from_file_location('cloud_schedule', Path(__file__).parents[1] / 'scripts/cloud_schedule.py')
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class LemonRenCountTests(unittest.TestCase):
    def jobs(self, value):
        when = dt.datetime.fromisoformat(value).replace(tzinfo=cs.TZ)
        return {k: (w, i) for w, i, k in cs.plan(when, rules=ss.default_rules(), is_workday=lambda d: d.weekday() < 5)}

    def test_dispatched_daily_1100_to_order_system(self):
        for day in ['2026-11-20', '2026-11-21', '2026-11-22']:  # 平日、週六、週日都跑
            self.assertEqual(self.jobs(day + 'T11:00').get('lemon_ren_count'),
                             ('jenny-smart/order-system:lemon-ren-count-schedule.yml', {}))
            self.assertNotIn('lemon_ren_count', self.jobs(day + 'T10:50'))
            self.assertNotIn('lemon_ren_count', self.jobs(day + 'T11:10'))
        self.assertEqual(ss.system_name('lemon_ren_count'), 'order-system')


if __name__ == '__main__':
    unittest.main()
