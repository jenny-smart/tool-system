"""【財報】營收總表→財報：營業額總表「財報總表」→ 財報股東損益表當月預估欄（2026-11 Jenny）。"""
from tools.finance_management.revenue_to_statement import plan_updates

MONTHS = [f"2026.{m:02d}" for m in range(1, 13)]


def _source():
    rows = [[] for _ in range(24)]
    rows[0] = ["服務基礎", "", ""] + MONTHS
    rows[13] = ["現金基礎", "", ""] + MONTHS
    for r, value in {2: 4800, 3: 40640, 6: 59350, 8: "-", 10: 3600}.items():
        rows[r - 1] = ["服務基礎", "", ""] + [0] * 9 + [value]
    for r, value in {15: 4800, 16: 55180, 18: "  -   ", 19: 59980}.items():
        rows[r - 1] = ["現金基礎", "", ""] + [0] * 9 + [value]
    return rows


def test_plan_maps_rows_and_month_column():
    header = ["新竹服務"] + [f"{m}{k}" for m in MONTHS for k in ("實際", "預估")]
    plans, notes = plan_updates(_source(), {"財務": header, "現金": header}, "202610")
    assert notes == []
    assert plans["財務"] == [(4, "U", 4800), (5, "U", 40640), (8, "U", 59350), (9, "U", 0), (11, "U", 3600)]
    assert plans["現金"] == [(4, "U", 4800), (5, "U", 55180), (8, "U", 0)]  # 18 儲值金預收，不是 19 總計


def test_missing_month_is_reported():
    plans, notes = plan_updates(_source(), {"財務": ["x"], "現金": ["x"]}, "202610")
    assert plans == {} and len(notes) == 2
