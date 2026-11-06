"""ATM 對帳 ②＋③ 每日 08:10：排程設定派送到 order-system 的 workflow（程式在 order-system）。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.scheduling import schedule_settings as ss

TZ = ZoneInfo("Asia/Taipei")


def test_rule_runs_daily_at_0810_and_dispatches_to_order_system():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "atm_match_reconcile")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 6, 8, 10, tzinfo=TZ)) == ["atm_match_reconcile"]
    assert due(datetime(2026, 11, 7, 8, 10, tzinfo=TZ)) == ["atm_match_reconcile"]  # 週六照跑
    assert due(datetime(2026, 11, 6, 8, 0, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 6, 8, 10, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/order-system", "atm-match-reconcile-schedule.yml")
    assert inputs == {}
    assert ss.system_name("atm_match_reconcile") == "order-system"
