"""新竹儲值金結算拆高雄：分不出新竹或高雄時用手機查近期訂單。"""
import pytest

from services.vip_workflow import VipStoredValueWorkflow, classify_hsinchu_row


@pytest.mark.parametrize("plan, address, expected", [
    ("儲值金50000", "新竹市東區光復路1號", "新竹"),
    ("儲值金50000", "新竹縣竹北市光明一路1號", "新竹"),
    ("儲值金50000", "高雄市三民區1號", "高雄"),
    ("儲值金50000", "臺南市東區1號", "高雄"),
    ("儲值金18900", "", "高雄"),
    ("儲值金50000", "", ""),                         # 共用方案又沒地址
    ("儲值金18900", "新竹縣竹北市1號", ""),           # 新竹地址卻是高雄方案
    ("儲值金50000", "苗栗縣竹南鎮1號", ""),
])
def test_classify(plan, address, expected):
    assert classify_hsinchu_row(plan, address) == expected


class _WS:
    def __init__(self, values):
        self.values, self.written = values, []

    def get_all_values(self):
        return self.values

    def clear(self):
        pass

    def update(self, rng, values, **_):
        self.written = list(values)


class _Sheets:
    def __init__(self, ws):
        self.ws = ws

    def open_by_id(self, _):
        ws = self.ws
        return type("SS", (), {"worksheets": lambda self: [ws]})()

    def write_values(self, ws, row, col, rows):
        ws.written += rows


def _wf(values, lookup):
    wf = VipStoredValueWorkflow.__new__(VipStoredValueWorkflow)
    wf.sheets = _Sheets(_WS(values))
    wf.phone_area_lookup = lookup
    wf._phone_area_cache, wf._phone_lookup_error = {}, ""
    return wf


def _row(name, phone, address, plan):
    return ["1", name, "", phone, address, "", "", plan]


ROWS = [["id", "姓名", "email", "電話", "地址", "LINE", "日期", "方案"],
        _row("甲", "0911111111", "新竹市東區1號", "儲值金50000"),
        _row("乙", "0922222222", "", "儲值金50000"),            # 手機查到高雄
        _row("丙", "0933333333", "", "儲值金50000"),            # 手機查不到 → 原規則留新竹
        _row("丁", "0944444444", "新竹縣竹北市1號", "儲值金18900")]  # 手機查到新竹


def test_phone_lookup_decides_unclear_rows_consistently():
    calls = []

    def lookup(phone):
        calls.append(phone)
        return {"0922222222": "高雄", "0944444444": "新竹"}.get(phone, "")

    wf = _wf(ROWS, lookup)
    kept, total = wf.filter_rows_by_h_column("x", keep_matching=False)   # 新竹
    assert (kept, total) == (3, 4) and [r[1] for r in wf.sheets.ws.written[1:]] == ["甲", "丙", "丁"]
    kept, _ = wf.filter_rows_by_h_column("x", keep_matching=True)        # 高雄
    assert kept == 1 and wf.sheets.ws.written[-1][1] == "乙"
    assert sorted(calls) == ["0922222222", "0933333333", "0944444444"]   # 每支手機只查一次


def test_lookup_failure_falls_back_to_old_rule():
    def lookup(phone):
        raise RuntimeError("登入失敗")

    wf = _wf(ROWS, lookup)
    kept, _ = wf.filter_rows_by_h_column("x", keep_matching=True)
    assert kept == 1 and wf.sheets.ws.written[-1][1] == "丁"   # 原規則：高雄方案算高雄
    assert wf._phone_lookup_error == "登入失敗"


def test_resolve_uses_other_rows_of_same_phone_and_only_looks_up_needed_rows():
    from services.vip_workflow import resolve_hsinchu_areas

    rows = [_row("甲", "0911", "高雄市三民區1號", "轉換"),
            _row("甲", "0911", "", "儲值金50000"),               # 同手機另一列地址是高雄 → 高雄，不查
            _row("乙", "0922", "", "轉換"),                       # 不需要查（needs_lookup 為假）→ 原規則新竹
            _row("丙", "0933", "", "儲值金50000")]               # 要查
    calls, logs = [], []
    areas = resolve_hsinchu_areas(rows, lambda p: calls.append(p) or "高雄",
                                  needs_lookup=lambda r: r[7].startswith("儲值金"), log=logs.append)
    assert areas == ["高雄", "高雄", "新竹", "高雄"] and calls == ["0933"]
    assert any("1 位" in m for m in logs) and any("1/1" in m for m in logs)
