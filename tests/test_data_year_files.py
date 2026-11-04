"""12 月資料在 1 月才處理時，財報／目標及review 仍寫回 12 月所屬年度的檔。"""

import importlib
import sys
from datetime import date

import pytest


def _real(monkeypatch, name):
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Svc:
    def __init__(self, titles):
        self.titles = titles

    def spreadsheets(self):
        return self

    def get(self, **_):
        return _Req(lambda: {"sheets": [{"properties": {"title": t}} for t in self.titles]})


def test_choose_file_id_for_year_uses_root_only_for_past_year(monkeypatch):
    yr = _real(monkeypatch, "tools.common.year_roots")
    monkeypatch.setattr(yr, "taipei_year", lambda: 2027)
    calls = []
    monkeypatch.setattr(yr, "choose_file_id", lambda *a, **k: calls.append(("choose", a, k)) or "CONFIGURED")
    monkeypatch.setattr(yr, "root_file_id", lambda cat, area, year, idx=0: calls.append(("root", year)) or f"ROOT{year}")

    assert yr.choose_file_id_for_year("財報", "台北", "CFG", None) == "CONFIGURED"
    assert yr.choose_file_id_for_year("財報", "台北", "CFG", 2027) == "CONFIGURED"
    assert yr.choose_file_id_for_year("財報", "台北", "CFG", 2026) == "ROOT2026"

    def missing(*_a, **_k):
        raise FileNotFoundError("沒有 2026 年度檔")

    monkeypatch.setattr(yr, "root_file_id", missing)
    with pytest.raises(RuntimeError, match="2026 年資料要寫回 2026 年度檔"):
        yr.choose_file_id_for_year("財報", "台北", "CFG", 2026)


def test_statement_and_review_follow_data_year(monkeypatch):
    reg = _real(monkeypatch, "tools.finance_management.statement_registry")
    rows = [["台北", "FIN2027", "", "", "", "", "MKT"], ["2026", "REVIEW2026"], ["2027", "REVIEW2027"]]
    monkeypatch.setattr(reg, "_read_registry", lambda: rows)
    monkeypatch.setattr(reg, "taipei_year", lambda: 2027)
    monkeypatch.setattr(reg, "get_sheets_service", lambda: _Svc(
        ["富邦更新", "台北財報", "2026review", "2027review", "2026總表", "2027總表"]))
    picked = []
    monkeypatch.setattr(reg, "choose_file_id_for_year",
                        lambda cat, area, sid, year=None, **k: picked.append((cat, sid, year)) or f"{sid}@{year}")
    reg._TITLES_CACHE.clear()

    assert reg.resolve_statement_location("台北", "富邦更新", year=2026) == ("FIN2027@2026", "富邦更新")
    assert reg.resolve_master_review_location(2026) == ("REVIEW2026@2026", "2026review")
    assert reg.resolve_review_location("台北", 2026) == ("REVIEW2026@2026", "台北財報")
    assert reg.resolve_master_review_location() == ("REVIEW2027@2027", "2027review")
    assert reg.resolve_marketing_expense_location("台北", year=2026) == ("MKT", "2026總表")
    assert ("財報", "FIN2027", 2026) in picked


def test_statement_years_splits_cross_year_range(monkeypatch):
    sy = _real(monkeypatch, "tools.finance_management.statement_years")
    monkeypatch.setattr(sy, "taipei_year", lambda: 2027)
    assert sy.statement_years() == [None]
    assert sy.statement_years(date(2027, 1, 1), date(2027, 1, 31)) == [None]
    assert sy.statement_years(date(2026, 12, 20), date(2027, 1, 5)) == [2026, None]
    assert sy.statement_years(date(2026, 12, 1), None) == [2026, None]
    assert sy.merge_year_results([
        {"updated_rows": 2, "inserted_rows": 1, "diagnostics": "2026"},
        {"updated_rows": 3, "inserted_rows": 0, "diagnostics": ""},
    ]) == {"updated_rows": 5, "inserted_rows": 1, "diagnostics": "2026"}


def test_review_update_uses_year_of_period(monkeypatch):
    _real(monkeypatch, "tools.finance_management.statement_registry")  # 其他測試可能放了假的模組
    ru = _real(monkeypatch, "tools.finance_management.review_update")
    seen = []
    monkeypatch.setattr(ru, "resolve_master_review_location", lambda year=None: seen.append(("master", year)) or ("id", "t"))
    monkeypatch.setattr(ru, "resolve_review_location", lambda area, year=None: seen.append((area, year)) or ("id", "t"))
    ru._resolve_location("年度review", "2026.12")
    ru._resolve_location("台北", "202612")
    ru._resolve_location("台北")
    assert seen == [("master", 2026), ("台北", 2026), ("台北", None)]


def test_cash_gap_december_reads_last_year_and_next_year_labels(monkeypatch):
    cg = _real(monkeypatch, "tools.finance_management.cash_gap")
    monkeypatch.setattr(cg, "taipei_year", lambda: 2027)
    header = [["h"] * 13]

    def row(day, label, amount, balance):
        r = [""] * 13
        r[cg.COL_B - 1] = day
        r[cg.COL_G - 1] = balance
        r[cg.COL_FUBON_LABEL - 1] = label
        r[cg.COL_FUBON_AMOUNT - 1] = amount
        return r

    reports = {
        ("富邦更新", 2026): header + [row("2026/12/31", "2026.12-內勤薪資", 100, 5000)],
        ("富邦更新", None): header + [row("2027/01/05", "2026.12-內勤薪資", 30, 9000)],
        ("元大更新", 2026): header,
        ("元大更新", None): header,
    }
    asked = []
    monkeypatch.setattr(cg, "_report_values", lambda area, report, year=None: asked.append((report, year)) or reports[(report, year)])
    years = []
    monkeypatch.setattr(cg, "marketing_expense_row", lambda area, month, r, year=None: years.append(year) or 0)
    monkeypatch.setattr(cg, "finance_revenue", lambda area, month, year=None: years.append(year) or 0)
    monkeypatch.setattr(cg, "_finance_column_value", lambda area, month, r, service=None, year=None: years.append(year) or 0)
    monkeypatch.setattr(cg, "newebpay_invoice_amount", lambda area, as_of: 0)

    result = cg.compute_cash_gap("台北", date(2026, 12, 31))
    assert result["富邦餘額"] == 5000          # 餘額只看 2026 年財報
    assert result["內勤薪資"] == 130           # 12 月薪資 1 月才匯：加上 2027 年財報的 2026.12 列
    assert ("富邦更新", 2026) in asked and ("富邦更新", None) in asked
    assert set(years) == {2026}
