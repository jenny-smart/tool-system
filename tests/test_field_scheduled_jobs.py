"""2026-11-05 Jenny：外場月排程／專員請款／新訓退款排程。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.field_management import scheduled_jobs as jobs
from tools.scheduling import schedule_settings as ss


def test_claim_period_by_day():
    assert jobs.claim_period(date(2026, 11, 15)) == "202611-1"
    assert jobs.claim_period(date(2026, 11, 30)) == "202611-2"


def test_pending_claim_blocks_skip_filled_period():
    header = ["A", "B", "C", "服務日期", "顧客姓名"] + [""] * 31
    header[35] = "給付期別"  # AJ

    def r(d="", c="", aj=""):
        row = [""] * 36
        row[3], row[4], row[35] = d, c, aj
        return row

    rows = [r("2026/11/01", "甲", "202610-2"),  # 2：已有期別
            r("2026/11/02", "乙"),              # 3
            r(),                                # 4：空白列
            r("2026/11/03", "丙"),              # 5
            r("2026/11/04", "丁", "202611-1"),  # 6
            r("", "戊")]                        # 7
    assert jobs.pending_claim_blocks(header, rows) == [(3, 5), (7, 7)]


def test_schedule_rows():
    rules = {r.key: r for r in ss.default_rules()}
    tw = ZoneInfo("Asia/Taipei")
    for key in ("field_roster_raise", "field_claim_review", "field_trainee_fee"):
        assert key in rules and not rules[key].errors and ss.FUNCTIONS[key][0] == "scheduled_field.yml"
    work = lambda d: True
    assert ss.matches_day(rules["field_claim_review"], date(2026, 11, 15), work)
    assert ss.matches_day(rules["field_claim_review"], date(2026, 11, 30), work)
    assert not ss.matches_day(rules["field_claim_review"], date(2026, 11, 16), work)
    assert ss.matches_day(rules["field_trainee_fee"], date(2026, 11, 6), work)  # 週五
    assert not ss.matches_day(rules["field_trainee_fee"], date(2026, 11, 5), work)
    assert ss.matches_day(rules["field_roster_raise"], date(2026, 11, 15), work)
