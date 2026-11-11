"""官網評價排程（上班日 07:00 清潔客訴、07:20 評價預約）：排程設定派送到 order-system 的 rating-schedule.yml。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tools.scheduling import schedule_settings as ss

TZ = ZoneInfo("Asia/Taipei")


@pytest.mark.parametrize("key,hm,job", [("rating_complaint_daily", (7, 0), "complaint"),
                                        ("rating_booking_daily", (7, 20), "booking")])
def test_rating_rules_run_workdays_and_dispatch_to_order_system(key, hm, job):
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == key)
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 11, *hm, tzinfo=TZ)) == [key]
    assert due(datetime(2026, 11, 8, *hm, tzinfo=TZ)) == []  # 週日略過
    assert due(datetime(2026, 11, 11, hm[0], hm[1] + 10, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 11, *hm, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/order-system", "rating-schedule.yml")
    assert inputs == {"job": job}
    assert ss.system_name(key) == "order-system"
