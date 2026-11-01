"""年度review 每月整理：欄位顯示規則、清掉舊分組、現金缺口區塊寫入位置與內容。"""
from datetime import date

from tools.finance_management import review_monthly as rm

# 台北財報 row1（A 標題、B 起每月實際／預估、Z/AA 年度合計）
HEADER = ["台北服務"] + [f"2026.{m:02d}{k}" for m in range(1, 13) for k in ("實際", "預估")] + ["2026實際", "2026預估", "#REF!"]
# 台北財報目前疊在一起的分組（T～Z 一組會連 10 月預估一起收起）
GROUPS = [{"depth": 1, "range": {"startIndex": s, "endIndex": e}} for s, e in
          [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (19, 26)]] + \
         [{"depth": 2, "range": {"startIndex": 20, "endIndex": 25}}]


def test_hidden_columns_rule_a_for_october():
    cols = rm.classify_header(HEADER)
    hidden = rm.hidden_columns(cols, (2026, 10))
    letters = [rm.column_letter(i) for i in hidden]
    # 1～9 月預估收起（C,E,…,S）；10 月實際 T 收起、預估 U 顯示；11、12 月 V～Y 收起
    assert "U" not in letters and "T" in letters and "S" in letters and "B" not in letters
    assert {"V", "W", "X", "Y"} <= set(letters)
    assert "Z" not in letters and "AA" not in letters  # 年度合計一律顯示
    assert rm.runs(hidden)[-2:] == [(18, 20), (21, 25)]  # 9 月預估＋10 月實際相鄰，收成一組


def test_layout_requests_clear_deepest_first_then_regroup():
    cols = rm.classify_header(HEADER)
    clear, regroup, hidden = rm.layout_requests(9, cols, GROUPS, (2026, 10))
    deletes = [r["deleteDimensionGroup"]["range"] for r in clear if "deleteDimensionGroup" in r]
    assert len(deletes) == len(GROUPS) and (deletes[0]["startIndex"], deletes[0]["endIndex"]) == (20, 25)
    assert clear[-1]["updateDimensionProperties"]["properties"] == {"hiddenByUser": False}
    added = [(r["addDimensionGroup"]["range"]["startIndex"], r["addDimensionGroup"]["range"]["endIndex"])
             for r in regroup if "addDimensionGroup" in r]
    assert (21, 25) in added  # 11～12 月連續收成一組
    assert all(not (s <= 20 < e) for s, e in added)  # 10 月預估（U）不在任何分組
    assert hidden == 9 + 1 + 4


def test_groups_outside_month_span_are_kept():
    cols = rm.classify_header(HEADER)
    clear, _regroup, _ = rm.layout_requests(9, cols, [{"depth": 1, "range": {"startIndex": 30, "endIndex": 35}}], (2026, 10))
    assert not any("deleteDimensionGroup" in r for r in clear)


def test_cash_gap_block_position_and_values():
    assert rm.column_letter(rm.block_start(1)) == "I" and rm.column_letter(rm.block_start(10)) == "CC"
    assert rm.column_letter(rm.block_start(9)) == "BU"
    cash = rm.cash_gap_values([
        ["", "台北", "台中", "桃園", "新竹", "高雄", "ALL"], ["日期", "2026/10/31"],
        ["富邦餘額", 100, 10, 1, 2, "錯誤：「財報」"], ["元大餘額", 5, 1, 0, 0, 3],
        ["行銷費用(廣告)", 10, 1, 1, 1, 0], ["行銷費用(服務)", 1, 0, 0, 0, 0],
        ["專員承攬費", 7, 0, 0, 0, 0], ["藍新發票金額", 41654, 4384, 10841, 1624, 1624],
    ])
    existing = [["", "台北", "台中", "桃園", "新竹", "高雄", "ALL"]] + [[]] * 18 + [["現金總金額", "=CD14-SUM(CD15:CD19)"]]
    data, skipped = rm.build_block_updates("現金缺口", date(2026, 10, 31), [["a"] * 7] * 8, cash, existing, "2026/10")
    cells = {d["range"].split("!")[1]: d["values"][0][0] for d in data}
    assert cells["CD14"] == 105 and "CH14" not in cells and "高雄 現金餘額" in skipped
    assert cells["CD23"] == 11 and cells["CD22"] == 7 and cells["CD38"] == 41654  # 藍新發票金額→38
    assert "CD26" not in cells and "CD40" not in cells and not any("藍新帳務" in x for x in skipped)
    assert cells["CC33"] == "2026/10-現金缺口"
    assert "CD20" not in cells and cells["CE20"] == "=CE14-SUM(CE15:CE19)"  # 已有公式不覆蓋
    assert any(d["range"].endswith("CC2:CI2") for d in data) and any(d["range"].endswith("CC3:CI10") for d in data)


def test_cash_gap_block_newebpay_balance_rows():
    cash = rm.cash_gap_values([
        ["", "台北", "台中", "桃園", "新竹", "高雄", "ALL"], ["日期", "2026/10/31"],
        ["藍新帳務日期", "2026-11-01 13:01:59", "", "2026-11-01 09:00:00"], ["藍新帳務金額", 461580, "", 1200],
    ])
    data, _ = rm.build_block_updates("現金缺口", date(2026, 10, 31), [], cash, [], "2026/10")
    cells = {d["range"].split("!")[1]: d["values"][0][0] for d in data}
    assert cells["CD39"] == "2026-11-01 13:01:59" and cells["CD40"] == 461580 and cells["CF40"] == 1200
    assert cells["CC39"] == "藍新帳務日期" and cells["CC40"] == "藍新帳務金額" and cells["CC38"] == "藍新發票金額"
