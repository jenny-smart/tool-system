"""【財報】營收總表→財報：依 A～C 欄找來源列、依財報 A 欄標題找目標列（2026-11 Jenny）。"""
from tools.finance_management.revenue_to_statement import HOLD_FILL, hold_requests, plan_updates

MONTHS = [f"2026.{m:02d}" for m in range(1, 13)]
HEADER = ["x"] + [f"{m}{k}" for m in MONTHS for k in ("實際", "預估")]


def _hsinchu():
    """新竹／桃園：月份從 D 欄 2026.01 起，現金基礎第 14 列有自己的標題列。"""
    def row(basis, service, item, value):
        return [basis, service, item] + [0] * 9 + [value]
    rows = [[] for _ in range(24)]
    rows[0] = ["服務基礎", "", ""] + MONTHS
    rows[1] = row("服務基礎", "專業清潔", "當月服務收現", 4800)
    rows[2] = row("服務基礎", "專業清潔", "當月服務上月預收", 40640)
    rows[5] = row("服務基礎", "專業清潔", "儲值金", 59350)
    rows[7] = row("服務基礎", "家電清潔", "總計", "  -   ")
    rows[8] = ["服務基礎", "收納服務", "總計"]  # 空白不貼
    rows[9] = row("服務基礎", "水洗服務", "總計", 3600)
    rows[13] = ["現金基礎", "", ""] + MONTHS
    rows[14] = row("現金基礎", "專業清潔", "當月服務收現", 4800)
    rows[15] = row("現金基礎", "專業清潔", "次月服務預收", 55180)
    rows[17] = row("現金基礎", "專業清潔", "儲值金預收", "-")
    rows[18] = row("現金基礎", "專業清潔", "總計", 59980)
    return rows


HSINCHU_FIN = [["新竹服務"], ["總營業收入"], ["專業清潔收入"], ["一般服務預收"], ["  一般服務本月"], ["  一般服務小計"],
               ["  VIP券收入"], ["儲值金收入"], ["家電清潔收入"], ["茉莉家事收入"], ["水洗清潔收入"], ["環境消毒收入"]]
HSINCHU_CASH = [["新竹現金"], ["總營業收入"], ["專業清潔收入"], ["  一般服務本月"], ["一般服務預收"], ["  一般服務小計"],
                ["  VIP券收入"], ["儲值金收入"], ["家電清潔收入"], ["茉莉家事收入"], ["水洗清潔收入"], ["環境消毒收入"]]


def test_hsinchu_layout():
    plans, notes = plan_updates(_hsinchu(), {"財務": HSINCHU_FIN, "現金": HSINCHU_CASH},
                                {"財務": HEADER, "現金": HEADER}, "202610")
    assert notes == []
    assert plans["財務"] == [(4, "U", 40640, False), (5, "U", 4800, False), (8, "U", 59350, False),
                             (9, "U", 0, False), (11, "U", 3600, False)]
    assert plans["現金"] == [(4, "U", 4800, False), (5, "U", 55180, False), (8, "U", 0, False)]


def test_taipei_layout_months_from_2020_and_cash_without_header_row():
    """台北：第 1 列月份從 2020.01 起（中間不連續），現金基礎沒有自己的標題列；財報現金沒有儲值金列。"""
    header = ["2026", "", "", "", "2020.01", "2025.12", "2026.10", "2026.11"]
    def row(basis, service, item, value):
        return [basis, service, item, "", 1, 2, value, "  -   "]
    source = [header,
              row("服務基礎", "專業清潔", "當月服務上月預收", 825300), row("服務基礎", "專業清潔", "當月服務收現", 973280),
              row("服務基礎", "專業清潔", "VIP服務", "-"), row("服務基礎", "專業清潔", "儲值金", 1154567),
              row("服務基礎", "收納服務", "總計", "-"), row("服務基礎", "水洗服務", "總計", 3600),
              row("服務基礎", "地毯服務", "總計", 5), [],
              row("現金基礎", "專業清潔", "當月服務收現", 980980), row("現金基礎", "專業清潔", "次月服務預收", 953350),
              row("現金基礎", "專業清潔", "儲值金預收", 2990000), row("現金基礎", "水洗服務", "總計", 27800)]
    fin = [["台北服務"], ["總營業收入"], ["專業清潔收入"], ["  一般服務預收"], ["  一般服務收入"], ["  一般服務收入小計"],
           ["  VIP券收入"], ["  儲值金收入"], ["家電清潔收入"], ["整理收納收入"], ["水洗清潔收入"], ["環境消毒收入"]]
    cash = [["台北現金"], ["總營業收入"], ["專業清潔收入"], ["一般服務收入"], ["一般服務預收"], ["一般服務收入小計"],
            ["VIP券預收"], ["家電清潔收入"], ["整理收納收入"], ["水洗清潔收入"], ["環境消毒收入"]]
    plans, notes = plan_updates(source, {"財務": fin, "現金": cash}, {"財務": HEADER, "現金": HEADER}, "202610")
    assert notes == []
    assert plans["財務"] == [(4, "U", 825300, False), (5, "U", 973280, False), (7, "U", 0, False),
                             (8, "U", 1154567, False), (10, "U", 0, False), (11, "U", 3600, False)]
    assert plans["現金"] == [(4, "U", 980980, False), (5, "U", 953350, False), (10, "U", 27800, False)]


def test_missing_month_is_reported():
    plans, notes = plan_updates(_hsinchu(), {}, {"財務": ["x"], "現金": ["x"]}, "202610")
    assert plans == {} and len(notes) == 2


def test_hold_cells_only_get_color_and_note():
    request = hold_requests(7, [(4, "U", 4800, True)], "202610")[0]["updateCells"]
    assert request["range"] == {"sheetId": 7, "startRowIndex": 3, "endRowIndex": 4,
                                "startColumnIndex": 20, "endColumnIndex": 21}
    cell = request["rows"][0]["values"][0]
    assert cell["userEnteredFormat"]["backgroundColor"] == HOLD_FILL and "4,800" in cell["note"]
    assert "userEnteredValue" not in cell and "userEnteredValue" not in request["fields"]
