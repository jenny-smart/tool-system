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


def test_generate_status_uses_latest_run_for_year():
    values = [
        ["執行時間", "新年度", "功能", "區域", "模式"],
        ["2026/10/09 7:17:17", "2027", "財務報表", "全區", "執行"],
        ["2026/10/18 03:51:17", "2027", "內勤表單", "高雄", "預覽"],
        ["2025/10/01 1:00:00", "2026", "內勤薪資", "全區", "執行"],
    ]
    status = {g["功能"]: g for g in nyc.generate_status(values, 2027, ["財務報表", "內勤表單", "內勤薪資"])}
    assert status["財務報表"]["狀態"] == "已執行"
    assert status["內勤表單"]["狀態"] == "只預覽"
    assert status["內勤薪資"]["狀態"] == "未執行"


def test_hardcoded_todo_and_missing_logs():
    values = [
        ["repo", "系統", "功能", "檔案", "行", "類型", "ID", "程式碼", "總表年度處理", "總表檔名"],
        ["tool-system", "財務", "富邦", "sheet_filter.py", "283", "ID", "x", "", "需重設（改依地區）", "ATM"],
        ["tool-system", "財務", "春酒", "spring.py", "41", "ID", "y", "", "改讀年度根目錄設定", ""],
    ]
    assert [h["項目"] for h in nyc.hardcoded_todo(values)] == ["tool-system sheet_filter.py:283（富邦）"]
    titles = ["tool-system執行Log-2027", "salary-system執行Log-2026"]
    assert nyc.missing_log_tabs(titles, 2027) == [
        "orders-system執行Log-2027", "order-system執行Log-2027", "salary-system執行Log-2027"]


def test_inventory_smoke(monkeypatch):
    class Sheets(FakeSheets):
        def get(self, spreadsheetId, range=None, fields=None):
            if fields:
                return _Req({"sheets": [{"properties": {"title": "tool-system執行Log-2027"}}]})
            return super().get(spreadsheetId, range)

    monkeypatch.setattr(year_roots, "check_year", lambda roots, year: [])
    monkeypatch.setattr(year_roots, "write_check_column", lambda *a, **k: None)
    sheets = Sheets({
        nyc.PARAM_SHEET: PARAMS,
        nyc.GENERATE_LOG_SHEET: [["執行時間", "新年度", "功能", "區域", "模式"],
                                 ["2026/10/09", "2027", "財務報表", "全區", "執行"]],
    })
    message, rows = nyc.inventory(sheets, "master", _Roots([]), 2027, ["財務報表", "內勤薪資"])
    assert "生成新年度未執行 1 項" in message and "執行Log未建立 3 張" in message
    blocks = {r["區塊"] for r in rows}
    assert {"生成新年度", "參數設定", "執行Log"} <= blocks
