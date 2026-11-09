"""salary-system 建立期別資料夾與檔案：每月 10、25 日 10:00，由排程設定派送到 salary-system 的 scheduler.yml。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.scheduling import schedule_settings as ss

TZ = ZoneInfo("Asia/Taipei")


def test_rule_runs_10th_and_25th_at_1000_and_dispatches_to_salary_system():
    row = next(r for r in ss.DEFAULT_ROWS if r[1] == "salary_create_period")
    rule = ss.parse_row(row)
    assert rule.enabled and rule.errors == []
    workday = lambda d: d.weekday() < 5
    due = lambda when: [r.key for r in ss.due([rule], when, workday)]
    assert due(datetime(2026, 11, 10, 10, 0, tzinfo=TZ)) == ["salary_create_period"]
    assert due(datetime(2026, 10, 25, 10, 0, tzinfo=TZ)) == ["salary_create_period"]  # 週日也照跑
    assert due(datetime(2026, 11, 10, 17, 10, tzinfo=TZ)) == []
    assert due(datetime(2026, 11, 30, 10, 0, tzinfo=TZ)) == []
    workflow, inputs = ss.dispatch_for(rule, datetime(2026, 11, 25, 10, 0, tzinfo=TZ))
    assert ss.split_workflow(workflow) == ("jenny-smart/salary-system", "scheduler.yml")
    assert inputs == {"action": "create_period"}
    assert ss.system_name("salary_create_period") == "salary-system"
    assert ss.EXTERNAL_DISPATCH_TOKENS["jenny-smart/salary-system"] == "SALARY_SYSTEM_DISPATCH_TOKEN"


def test_router_workflow_passes_salary_token():
    from pathlib import Path

    text = (Path(__file__).parents[1] / ".github/workflows/cloud_schedule.yml").read_text(encoding="utf-8")
    assert "SALARY_SYSTEM_DISPATCH_TOKEN: ${{ secrets.SALARY_SYSTEM_DISPATCH_TOKEN }}" in text
