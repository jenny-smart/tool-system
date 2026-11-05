"""2026-11-05 Jenny：外場月排程／專員請款／新訓退款排程。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tools.field_management import scheduled_jobs as jobs
from tools.scheduling import schedule_settings as ss


def test_claim_period_by_day():
    assert jobs.claim_period(date(2026, 11, 15)) == "202611-1"
    assert jobs.claim_period(date(2026, 11, 30)) == "202611-2"


def test_roster_period_advanced_first_day():
    assert jobs.roster_period(date(2026, 11, 13)) == "202611"
    assert jobs.roster_period(date(2026, 11, 2)) == "202611"
    assert jobs.roster_period(date(2026, 10, 30)) == "202611"  # 11/1 週日提前
    assert jobs.roster_period(date(2026, 12, 31)) == "202701"


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
    for key in ("field_roster", "field_raise", "field_claim_review", "field_trainee_fee"):
        assert key in rules and not rules[key].errors and ss.FUNCTIONS[key][0] == "scheduled_field.yml"
    work = lambda d: True
    assert ss.matches_day(rules["field_claim_review"], date(2026, 11, 15), work)
    assert not ss.matches_day(rules["field_claim_review"], date(2026, 11, 30), work)
    # 遇例假日提前：2026/11/15 是週日 → 11/13（五）執行
    weekday = lambda d: d.weekday() < 5
    assert ss.matches_day(rules["field_claim_review"], date(2026, 11, 13), weekday)
    assert not ss.matches_day(rules["field_claim_review"], date(2026, 11, 15), weekday)
    assert ss.matches_day(rules["field_roster"], date(2026, 11, 13), weekday)
    assert ss.matches_day(rules["field_roster"], date(2026, 10, 30), weekday)  # 11/1 週日 → 10/30
    assert ss.matches_day(rules["field_raise"], date(2026, 11, 13), weekday)
    assert not ss.matches_day(rules["field_raise"], date(2026, 11, 2), weekday)
    assert ss.matches_day(rules["field_trainee_fee"], date(2026, 11, 6), work)  # 週五
    assert not ss.matches_day(rules["field_trainee_fee"], date(2026, 11, 5), work)
    assert "field_roster_raise" not in rules and "field_roster_raise" in ss.RETIRED_KEYS


def test_upgrade_outdated_rows_in_sheet():
    """Sheet 裡還是 #441 舊預設的列自動換成新預設；被改過的列不動。"""
    written = []

    class Svc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def batchUpdate(self, spreadsheetId, body):
            written.extend(body["data"])
            return self

        def execute(self):
            return {}

    rows = [["field_all", "外場排程", "05:00", "每日", "", "照跑"],
            ["field_roster_raise", "x", "06:00", "每月", "15", "照跑"],
            ["field_claim_review", "x", "06:00", "每月", "15,月底", "照跑"],
            ["field_trainee_fee", "x", "06:00", "每週", "五", "照跑"]]
    ss._upgrade_outdated_rows(Svc(), "id", rows, log=lambda *_: None)
    assert [d["range"] for d in written] == ["'排程設定'!B3:I3", "'排程設定'!F4:G4"]
    assert written[0]["values"][0][:6] == ["field_roster", "【月排程】新增專員名冊（台北／台中）", "06:00", "每月", "1,15", "提前"]
    assert written[1]["values"][0] == ["15", "提前"]
    assert rows[1] == ["field_roster"]
    written.clear()
    ss._upgrade_outdated_rows(Svc(), "id", [["field_claim_review", "x", "07:00", "每月", "15", "照跑"]], log=lambda *_: None)
    assert written == []



def test_finance_rows_advance_on_holiday():
    rules = {r.key: r for r in ss.default_rules()}
    for key in ("fixed_expense_remit", "fixed_expense_mail", "accounting_reminder"):
        assert rules[key].holiday == "提前"
    written = []

    class Svc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def batchUpdate(self, spreadsheetId, body):
            written.extend(body["data"])
            return self

        def execute(self):
            return {}

    rows = [["fixed_expense_remit", "【財報】各區固定費用匯款（富邦匯款給台北）", "04:00", "每月", "10", "照跑"],
            ["fixed_expense_mail", "x", "09:00", "每月", "5,8,10", "照跑"],
            ["accounting_reminder", "x", "10:00", "單月", "10", "照跑"]]
    ss._upgrade_outdated_rows(Svc(), "id", rows, log=lambda *_: None)
    assert [(d["range"], d["values"][0]) for d in written] == [
        ("'排程設定'!F2:G2", ["10", "提前"]), ("'排程設定'!F3:G3", ["5,8,10", "提前"]), ("'排程設定'!F4:G4", ["10", "提前"])]
