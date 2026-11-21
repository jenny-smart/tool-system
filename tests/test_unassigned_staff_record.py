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

    def test_dispatched_hourly_0830_to_1730(self):
        for key, wf in [('unassigned_staff_record', 'unassigned-staff-record-schedule.yml'),
                        ('unassigned_reserve_create', 'unassigned-reserve-schedule.yml')]:
            for t in ['08:30', '12:30', '17:30']:
                self.assertEqual(self.jobs('2026-11-22T' + t).get(key), ('jenny-smart/order-system:' + wf, {}))
            for t in ['07:30', '08:40', '18:30']:
                self.assertNotIn(key, self.jobs('2026-11-22T' + t))
            self.assertEqual(ss.system_name(key), 'order-system')

    def test_default_row_exists_so_router_appends_it(self):
        keys = [row[1] for row in ss.DEFAULT_ROWS]
        self.assertIn('unassigned_staff_record', keys)
        self.assertIn('unassigned_reserve_create', keys)


if __name__ == '__main__':
    unittest.main()
