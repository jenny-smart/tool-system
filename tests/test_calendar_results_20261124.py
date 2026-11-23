"""2026-11-24 Jenny：行事曆只記結果、不重複；資產快照記 jenny@hers.com.tw；富邦明細記筆數金額。"""
from tools.bank_statement.capture import CapturedTable
from tools.bank_statement.fubon_agent import statement_totals
from tools.scheduled_daily.performance_report_runner import _money
from tools.scheduling import calendar_record as cr


def test_self_recorded_success_skipped_failure_kept():
    assert cr.skip_record("stored_value_notice_daily", {"conclusion": "success"})
    assert cr.skip_record("unassigned_reserve_create", {"conclusion": "success"})
    assert not cr.skip_record("stored_value_notice_daily", {"conclusion": "failure"})
    assert not cr.skip_record("performance_report", {"conclusion": "success"})


def test_invest_snapshot_goes_to_hers():
    assert "invest_snapshot" in cr.HERS_KEYS


def test_statement_totals():
    t = CapturedTable(headers=["交易日", "帳務日", "說明", "支出", "存入", "餘額", "備註"],
                      rows=[["1", "1", "a", "1,000", "", "9", ""], ["1", "1", "b", "", "2,500", "9", ""],
                            ["1", "1", "c", "", "500", "9", ""]])
    assert statement_totals(t) == {"count": 3, "in_count": 2, "in_total": 3000.0, "out_count": 1, "out_total": 1000.0}


def test_money():
    assert _money(1234567.4) == "1,234,567"
    assert _money("") == "0"


def test_region_summary_lines_only():
    log = "\n".join([
        "##[group]Run python -u -m shared.lemon_ren_clear_schedule",
        "##[endgroup]",
        "在 2027-01-18 所在那週的清潔班表裡，找到 0 筆未配班清單中的檸檬人佔用紀錄",
        "在 2027-01-25 所在那週的清潔班表裡，找到 0 筆未配班清單中的檸檬人佔用紀錄",
        "台北／清空檸檬人：3 人，2 天有清到資料，移除 5 筆勾選",
        "台中／清空檸檬人：未配班清單沒有檸檬人",
        "合計／清空檸檬人：共 3 人，移除 5 筆勾選",
    ])
    assert cr.result_lines(log) == [
        "台北／清空檸檬人：3 人，2 天有清到資料，移除 5 筆勾選",
        "台中／清空檸檬人：未配班清單沒有檸檬人",
        "合計／清空檸檬人：共 3 人，移除 5 筆勾選",
    ]
