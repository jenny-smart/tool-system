"""客人備註非空白每日 10:00（上班日）：排程設定派送到 order-system 的 workflow（程式在 order-system）。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.scheduling import schedule_settings as ss

TZ = ZoneInfo("Asia/Taipei")


def test_rule_runs_workdays_at_1000_and_dispatches_to_order_system():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "customer_memo_daily")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 10, 10, 0, tzinfo=TZ)) == ["customer_memo_daily"]
    assert due(datetime(2026, 11, 8, 10, 0, tzinfo=TZ)) == []  # 週日略過
    assert due(datetime(2026, 11, 10, 9, 50, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 10, 10, 0, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/order-system", "customer-memo-schedule.yml")
    assert inputs == {}
    assert ss.system_name("customer_memo_daily") == "order-system"


def test_stored_value_notice_runs_workdays_at_0820_and_dispatches_to_order_system():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "stored_value_notice_daily")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 10, 8, 20, tzinfo=TZ)) == ["stored_value_notice_daily"]
    assert due(datetime(2026, 11, 8, 8, 20, tzinfo=TZ)) == []  # 週日略過
    assert due(datetime(2026, 11, 10, 8, 10, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 10, 8, 20, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/order-system", "stored-value-notice-schedule.yml")
    assert ss.system_name("stored_value_notice_daily") == "order-system"


def test_weekend_reminder_confirm_sync_runs_hourly_from_1000_including_weekends():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "weekend_reminder_confirm_sync")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 13, 10, 0, tzinfo=TZ)) == ["weekend_reminder_confirm_sync"]
    assert due(datetime(2026, 11, 13, 18, 0, tzinfo=TZ)) == ["weekend_reminder_confirm_sync"]
    assert due(datetime(2026, 11, 14, 11, 0, tzinfo=TZ)) == ["weekend_reminder_confirm_sync"]  # 週六照跑
    assert due(datetime(2026, 11, 13, 9, 0, tzinfo=TZ)) == []
    assert due(datetime(2026, 11, 13, 10, 30, tzinfo=TZ)) == []
    assert due(datetime(2026, 11, 13, 19, 0, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 13, 10, 0, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/order-system", "weekend-reminder-confirm-sync.yml")
    assert inputs == {}
    assert ss.system_name("weekend_reminder_confirm_sync") == "order-system"
