"""記錄未配班人員：排程設定每日 11:10 派送到 order-system，並自動補進「排程設定」分頁。"""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

from tools.scheduling import schedule_settings as ss

spec = importlib.util.spec_from_file_location('cloud_schedule', Path(__file__).parents[1] / 'scripts/cloud_schedule.py')
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class UnassignedStaffRecordTests(unittest.TestCase):
    def jobs(self, value):
        when = dt.datetime.fromisoformat(value).replace(tzinfo=cs.TZ)
        return {k: (w, i) for w, i, k in cs.plan(when, rules=ss.default_rules(), is_workday=lambda d: d.weekday() < 5)}

    def test_dispatched_daily_1110_to_order_system(self):
        for day in ['2026-11-21', '2026-11-22', '2026-11-23']:
            self.assertEqual(self.jobs(day + 'T11:10').get('unassigned_staff_record'),
                             ('jenny-smart/order-system:unassigned-staff-record-schedule.yml', {}))
            self.assertNotIn('unassigned_staff_record', self.jobs(day + 'T11:00'))
        self.assertEqual(ss.system_name('unassigned_staff_record'), 'order-system')

    def test_default_row_exists_so_router_appends_it(self):
        self.assertIn('unassigned_staff_record', [row[1] for row in ss.DEFAULT_ROWS])


if __name__ == '__main__':
    unittest.main()
