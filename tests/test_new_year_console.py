from tools.annual_rollover import new_year_console as nyc
from tools.common import year_roots


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeSheets:
    """只模擬 values().get / values().batchUpdate。"""

    def __init__(self, tables):
        self.tables = tables
        self.writes = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        title = range.split("'")[1]
        return _Req({"values": self.tables.get(title, [])})

    def batchUpdate(self, spreadsheetId, body):
        self.writes.extend(body["data"])
        return _Req({})


PARAMS = [
    ["執行系統", "執行功能", "地區", "設定項目", "設定值", "啟用"],
    ["財務管理", "鯨躍字軌配號", "台北", "線上單張開立保留本數", "3", "TRUE"],
    ["財務管理", "年度review", "共用", "年度彙總列代號", "2026", "TRUE"],
    ["外場排程系統", "外場排班統計表", "台北／台中", "名冊預設年度", "2027", "TRUE"],
]


def test_update_year_params_writes_only_outdated_rows():
    sheets = FakeSheets({nyc.PARAM_SHEET: PARAMS})
    rows = nyc.update_year_params(sheets, "master", 2027)
    assert sheets.writes == [{"range": f"'{nyc.PARAM_SHEET}'!E3", "values": [["2027"]]}]
    results = {r["項目"]: r["結果"] for r in rows}
    assert results["財務管理｜年度review｜年度彙總列代號"] == "2026 → 2027"
    assert results["外場排程系統｜外場排班統計表｜名冊預設年度"] == "已是新年度"


def test_update_year_params_reports_missing_row():
    sheets = FakeSheets({nyc.PARAM_SHEET: PARAMS[:3]})
    rows = nyc.update_year_params(sheets, "master", 2027)
    assert any(r["結果"] == "參數設定找不到這一列" for r in rows)


def _rule(row, mode, results=None, category="財報"):
    return year_roots.Rule(row=row, category=category, areas=["台北"], root_id="x", year_folder="{YYYY}",
                           sub_folder="", file_names=["{YYYY}台北財報"], since_year=0, mode=mode,
                           results=results or {})


class _Roots:
    def __init__(self, rules):
        self.rules = rules


def _check(row, result):
    return {"列": str(row), "地區": "台北", "檔名規則": "{YYYY}台北財報", "結果": result}


def test_plan_root_switch_rules():
    ok = {"台北 2026": "2026/11/18 10:00 台北 2026 一致"}
    bad = {"台北 2026": "2026/11/18 10:00 台北 2026 不一致"}
    roots = _Roots([
        _rule(4, "比對", ok),          # 可切換
        _rule(5, "比對", ok),          # 新年度缺檔
        _rule(6, "比對", bad),         # L 欄不一致
        _rule(7, "根目錄", ok),        # 已是根目錄
        _rule(8, "原設定"),            # 原設定不動
        _rule(9, "比對"),              # 尚未比對但新年度都找到
    ])
    checks = [_check(4, "找到"), _check(5, "找不到：沒有 2027 資料夾"), _check(6, "找到"),
              _check(7, "找到"), _check(8, "找到"), _check(9, "依期別自動建立（不需檢查）")]
    plan = {p["列"]: p for p in nyc.plan_root_switch(roots, 2027, checks)}
    assert plan["4"]["判斷"] == "可切換"
    assert plan["5"]["判斷"] == "不切換" and "2027 找不到" in plan["5"]["原因"]
    assert plan["6"]["判斷"] == "不切換" and "不一致" in plan["6"]["原因"]
    assert plan["7"]["判斷"] == "已是根目錄"
    assert plan["8"]["判斷"] == "不切換"
    assert plan["9"]["判斷"] == "可切換"

    sheets = FakeSheets({})
    assert nyc.switch_ready_rows(sheets, "master", list(plan.values())) == [4, 9]
    assert [w["range"] for w in sheets.writes] == [f"'{year_roots.ROOT_SHEET}'!K4", f"'{year_roots.ROOT_SHEET}'!K9"]
    assert all(w["values"] == [["根目錄"]] for w in sheets.writes)


def test_open_checklist_items_skips_done_and_blank():
    values = [
        ["序號", "類別", "項目", "系統", "何時做", "怎麼做", "對應", "已完成", "完成日期", "備註"],
        ["1", "生成新年度", "財務報表", "", "10 月", "", "", "FALSE", "", "已執行"],
        ["2", "生成新年度", "清潔用品", "", "10 月", "", "", "TRUE"],
        ["", "", "", "", "", "", "", "FALSE"],
    ]
    items = nyc.open_checklist_items(values)
    assert [i["序號"] for i in items] == ["1"]


def test_read_param_respects_disabled(monkeypatch):
    import tools.common.config_loader as cl

    rows = PARAMS + [["共用", "測試", "共用", "停用項", "X", "FALSE"]]
    monkeypatch.setattr(cl, "get_master_spreadsheet_id", lambda: "master")
    monkeypatch.setattr(cl, "get_sheets_service", lambda: FakeSheets({nyc.PARAM_SHEET: rows}))
    assert nyc.read_param("財務管理", "年度review", "年度彙總列代號", "2026") == "2026"
    assert nyc.read_param("外場排程系統", "外場排班統計表", "名冊預設年度", "2026") == "2027"
    assert nyc.read_param("共用", "測試", "停用項", "D") == "D"
    assert nyc.read_param("沒有", "這", "列", "D") == "D"
