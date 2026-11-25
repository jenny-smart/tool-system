from datetime import date

from tools.field_management import recruit_104_stats as r
from tools.scheduling import schedule_settings as ss

JOB = {"switch": "on", "job": "家事清潔人員-無經驗可", "openDate": "2026/02/23", "poOpenedDays": 209,
       "applyCount": [0, 0, 0, 1, 1, 1, 1], "applyTotal": 4, "applyAverage": 0.57,
       "applyLowValue": 0.04, "applyHighValue": 0.21,
       "clickCount": [35, 20, 16, 27, 27, 41, 20], "clickTotal": 186, "clickAverage": 26.57,
       "clickLowValue": 1.37, "clickHighValue": 6.86}
CLOSED = dict(JOB, switch="off", job="【台北】內勤行政助理(無經驗可)(已關閉)", applyCount=[-1] * 7,
              clickCount=[-1] * 7, applyTotal=0, clickTotal=0, applyAverage=0, clickAverage=0)
META = {"poStartDate": "2026/04/25", "poEndDate": "2027/05/10", "poPeriod": 380}


def test_export_rows_match_104_excel_layout():
    rows = r.export_rows(META, [JOB, CLOSED], date(2026, 11, 13), date(2026, 11, 19))
    assert rows[0] == ["目前刊期：2026/04/25~2027/05/10(刊登天數：380天)"]
    assert rows[1] == ["查詢日期：2026/11/13~2026/11/19"]
    assert rows[3] == ["家事清潔人員-無經驗可"]
    assert rows[4] == ["職務最近開啟日：2026/02/23(刊期內累計刊登209天)"]
    assert rows[5] == ["", "11/13", "11/14", "11/15", "11/16", "11/17", "11/18", "11/19", "總計", "平均", "同業低標", "同業高標"]
    # 副表公式：第 7 列＝第一個職務的主動應徵履歷數，每個職務 6 列
    assert rows[6] == ["主動應徵履歷數", 0, 0, 0, 1, 1, 1, 1, 4, 0.57, 0.04, 0.21]
    assert rows[7][:2] == ["職務頁瀏覽數(PV)", 35] and rows[7][8] == 186
    assert rows[12] == ["主動應徵履歷數", "-", "-", "-", "-", "-", "-", "-", 0, 0, 0.04, 0.21]
    assert all(len(row) <= 12 for row in rows)


def test_windows_normal_friday_is_one_week():
    assert r.windows(date(2026, 11, 19), date(2026, 11, 26)) == [(date(2026, 11, 20), date(2026, 11, 26))]


def test_windows_after_early_run_fill_gap_without_skipping():
    # 上週提前到週三跑過（寫到週三），本週五跑：補週四～本週四，分兩段（104 一次最多 7 天）
    assert r.windows(date(2026, 11, 25), date(2026, 12, 3)) == [
        (date(2026, 11, 26), date(2026, 12, 2)), (date(2026, 11, 27), date(2026, 12, 3))]
    assert r.windows(date(2026, 11, 26), date(2026, 11, 26)) == []


def test_last_date_column_and_stats_columns():
    row = ["", ""] + [""] * 11 + ["台北", "02/27", "2026/11/18", "2026/11/19", ""]
    assert r.last_date_column(row) == (16, date(2026, 11, 19))
    days = [date(2026, 11, 20 + i) for i in range(7)]
    sub = [["11/20"] * 7, [1] * 7, [10] * 7, [0.1] * 7, [], ["11/20"] * 7, [2] * 7, [20] * 7, [0.1] * 7, []]
    cols = r.stats_columns(sub, days, days[2:], 17)
    assert [c[0] for c in cols] == [17, 18, 19, 20, 21]
    col, day, values = cols[0]
    assert day == date(2026, 11, 22) and len(values) == 19
    assert values[0] == "2026/11/22" and values[1] == 1 and values[5] == "2026/11/22" and values[6] == 2
    assert values[15] == "2026/11/22" and values[16] == ""  # 第 4 區沒資料就空白


def test_week_total_formula_like_JT():
    assert r.week_total_formulas(279) == {6: "=SUM(JN13:JT13)", 7: "=SUM(JN18:JT18)",
                                          8: "=SUM(JN23:JT23)", 9: "=SUM(JN28:JT28)"}


def _rule():
    return ss.parse_row([True, "recruit_104_weekly", "104", "04:40", "每週", "五", "提前", ss.MODE_QUEUE, ""])


def test_weekly_earlier_moves_friday_holiday_to_thursday():
    holidays = {date(2027, 1, 1), date(2027, 2, 5), date(2027, 2, 4)}  # 例：元旦週五；連假週四、週五

    def workday(d):
        return d.weekday() < 5 and d not in holidays

    rule = _rule()
    assert not rule.errors
    assert ss.matches_day(rule, date(2026, 11, 27), workday)          # 一般週五
    assert not ss.matches_day(rule, date(2026, 11, 26), workday)
    assert ss.matches_day(rule, date(2026, 12, 31), workday)          # 元旦週五 → 週四
    assert not ss.matches_day(rule, date(2027, 1, 1), workday)
    assert ss.matches_day(rule, date(2027, 2, 3), workday)            # 週四五都放 → 週三
    assert not ss.matches_day(rule, date(2027, 2, 4), workday)


def test_registered_everywhere():
    from tools import local_agent

    assert "recruit_104_weekly" in ss.FUNCTIONS
    assert any(row[1] == "recruit_104_weekly" for row in ss.DEFAULT_ROWS)
    assert local_agent.command_for({"action": "recruit104.weekly_stats", "params_json": "{}"})[2] == \
        "tools.field_management.recruit_104_stats"
