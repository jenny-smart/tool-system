"""2026-11-04：工具包押金改用統計月份、固定費用轉帳篩選、review 公式只改合計欄。"""
import importlib
import sys
from datetime import date

from tools.finance_management import deposit_report as dr
from tools.scheduled_finance import runner


def _review_update(monkeypatch):
    # 其他測試可能在 sys.modules 放了假的 statement_registry，這裡載入真正的模組
    monkeypatch.delitem(sys.modules, "tools.finance_management.statement_registry", raising=False)
    monkeypatch.delitem(sys.modules, "tools.finance_management.review_update", raising=False)
    return importlib.import_module("tools.finance_management.review_update")


def _row(**cells):
    values = [""] * 26
    for key, value in cells.items():
        values[ord(key) - 65] = value
    return values


def test_month_rows_and_replace_plan():
    values = [_row(A="姓名"),
              _row(A="甲", U="上課：甲＋共1人", V="202609上課", W=2000, X=0),
              _row(A="乙", U="上課：乙/丙＋共2人", V="202610上課", W=4000, X=0),
              _row(A="丙", U="已退：丁＋共1人", V="202610已退", W=0, X=2000),
              _row(A="丁")]
    assert dr.month_rows(values, "202610") == [3, 4]
    # 前月重算後只剩一筆、而且內容變了：覆蓋第 3 列，第 4 列清空
    writes, clears = dr.plan_month_update(values, [["上課：乙＋共1人", "202610上課", 2000, 0]], "202610", "x")
    assert writes == [(3, ["上課：乙＋共1人", "202610上課", 2000, 0])] and clears == [4]
    # 當月沒有既有的列：接在 U 欄最後一列之後
    writes, clears = dr.plan_month_update(values, [["上課：丁＋共1人", "202611上課", 2000, 0]], "202611", "x")
    assert writes == [(5, ["上課：丁＋共1人", "202611上課", 2000, 0])] and clears == []
    # 內容一樣不重寫
    same = [["上課：乙/丙＋共2人", "202610上課", 4000, 0], ["已退：丁＋共1人", "202610已退", 0, 2000]]
    assert dr.plan_month_update(values, same, "202610", "x") == ([], [])


def test_fixed_expense_payments_filter():
    items = [
        {"kind": "payment", "memo": "2026.10-Amazon Web Services，USD 12", "area": "台北", "row": 3},
        {"kind": "payment", "memo": "2026.11-台灣連線", "area": "台北", "row": 4},
        {"kind": "payment", "memo": "2026.11-辦公室租金管理費", "area": "台中", "row": 5},
        {"kind": "payment", "memo": "2026.10-清潔用品", "area": "台北", "row": 6},
        {"kind": "payment", "memo": "2026.08-眾點", "area": "台北", "row": 7},
        {"kind": "deposit", "memo": "2026.11-辦公室租金", "area": "台北", "row": 8},
    ]
    rows = [i["row"] for i in runner.fixed_expense_payments(items, date(2026, 11, 10))]
    assert rows == [3, 4, 5]


def test_review_only_rewrites_total_column(monkeypatch):
    ru = _review_update(monkeypatch)
    header = ["台北服務"] + [f"2026.{m:02d}{k}" for m in range(1, 13) for k in ("實際", "預估")] + ["2026實際", "2026預估"]
    formula = '=SUMIF(B$1:Q2,"*預估*",B2:Q2)'
    other = '=SUMIF(B$1:Q3,"*預估*",B3:Q3)'

    class Req:
        def __init__(self, data):
            self.data = data

        def execute(self):
            return self.data

    class Values:
        def get(self, spreadsheetId, range, valueRenderOption):
            if range.endswith("!1:1"):
                return Req({"values": [header]})
            row2 = [""] * 26 + [formula]
            row3 = [""] * 25 + [other]
            return Req({"values": [row2, row3]})

    class Service:
        def spreadsheets(self):
            return self

        def values(self):
            return Values()

    monkeypatch.setattr(ru, "_resolve_location", lambda area, year_month=None: ("id", "台北"))
    monkeypatch.setattr(ru, "get_sheets_service", lambda: Service())
    changes = ru.preview_review_formula_updates("台北", "2026.10")
    assert changes == [{"cell": "AA2", "old_formula": formula, "new_formula": '=SUMIF(B$1:U2,"*預估*",B2:U2)'}]
    assert ru.total_formula_column("年度review") == "AB"
