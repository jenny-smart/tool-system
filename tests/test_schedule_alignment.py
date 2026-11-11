"""程式依主控表「排程設定」的時間週期判斷（2026-11-11）：
月初檢查等 month_start_queue 排入、台北查信最後一次（遇例假日提前）才提醒、鯨躍字軌每月執行。"""
from datetime import date

from tools.finance_management import month_start_check as chk
from tools.scheduled_finance import runner
from tools.scheduling import schedule_settings as ss

HOLIDAYS = {date(2027, 1, 1)}


def workday(day):
    return day.weekday() < 5 and day not in HOLIDAYS


def test_month_start_agent_rows_wait_for_queue_hour():
    rows = [{"key": "pdf", "done": False, "remind": True, "todo": ["台北"]},
            {"key": "prepaid_report", "done": False, "remind": True, "todo": ["台中"]}]
    assert [r["key"] for r in chk.problems(rows, hour=8, start_hour=9)] == ["prepaid_report"]
    assert [r["key"] for r in chk.problems(rows, hour=9, start_hour=9)] == ["pdf", "prepaid_report"]


def test_queue_hour_reads_schedule(monkeypatch):
    rule = ss.parse_row([True, "month_start_queue", "x", "09:00", "每月", "1", "照跑", ss.MODE_QUEUE])
    monkeypatch.setattr(ss, "get_rule", lambda *_a, **_k: rule)
    assert chk.queue_hour() == 9
    monkeypatch.setattr(ss, "get_rule", lambda *_a, **_k: None)
    assert chk.queue_hour() == chk.MONTH_START_QUEUE_HOUR == 9


def test_fixed_expense_mail_last_run_moves_earlier_on_holiday():
    rule = ss.parse_row([True, "fixed_expense_mail", "x", "09:00", "每月", "5,8,10", "提前", ss.MODE_DIRECT])
    # 2027/1/10 週日、1/9 週六 → 提前到 1/8（五）就是最後一次
    assert runner.fixed_expense_mail_is_last_run(date(2027, 1, 8), rule, workday)
    assert not runner.fixed_expense_mail_is_last_run(date(2027, 1, 5), rule, workday)
    # 2026/11/10 週二照常
    assert runner.fixed_expense_mail_is_last_run(date(2026, 11, 10), rule, workday)
    assert not runner.fixed_expense_mail_is_last_run(date(2026, 11, 9), rule, workday)
    # 照跑（不提前）時 10 日才算
    rule.holiday = ss.HOLIDAY_RUN
    assert not runner.fixed_expense_mail_is_last_run(date(2027, 1, 8), rule, workday)


def test_mof_target_monthly():
    assert runner.mof_target(date(2026, 12, 20)) == ("202701", "116", "01-02")  # 雙月 → 下一期
    assert runner.mof_target(date(2026, 11, 20)) == ("202611", "115", "11-12")  # 單月 → 本期（補做檢查）
    assert runner.mof_target(date(2026, 10, 20)) == ("202611", "115", "11-12")  # 原本雙月的結果不變
