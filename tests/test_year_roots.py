from datetime import date

from tools.common import tw_holidays
from tools.common.year_roots import YearRoots, find_rule, parse_rules, resolve

FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"


class _Req:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeDrive:
    def __init__(self, items):
        self.items = items
        self.list_calls = 0

    def files(self):
        return self

    def list(self, q, **_):
        self.list_calls += 1
        parent = q.split("'")[1]
        return _Req({"files": [i for i in self.items if i["parent"] == parent]})


HEADER = [["年度根目錄設定"], ["說明"], ["類別", "地區", "根目錄ID", "根目錄名稱", "年度資料夾", "子資料夾",
                                       "檔名", "適用起年", "使用", "備註", "模式", "結果"]]


def _values(mode="比對", since="2026"):
    return HEADER + [
        ["內勤工作表單", "台北", "ROOT", "04", "{YYYY}專員回報專區", "—", "{YYYY}台北內勤工作表單", since, "", "", mode],
        ["名冊薪資", "台北", "R2", "01", "{YYYY}專員名冊薪資", "—",
         "{YYYY}專員名冊與時數-台北；{YYYY}專員薪資相關-台北", "2026", "", "", mode],
        ["財報", "台北／台中", "FIN", "02", "{YYYY}年", "—", "{地區}{YYYY}財報", "2026", "", "", mode],
        ["行銷費用總管理", "共用", "MKT", "固定檔", "—", "—", "工作表：{YYYY}廣告、{YYYY}總表", "2026", "", "", mode],
        ["大掃除", "共用", "DC", "大掃除", "{YYYY}", "—",
         "{YYYY}年終大掃除系統調整；{YYYY}年終大掃除VIP", "2026", "", "", mode],
    ]


def _drive():
    return FakeDrive([
        {"id": "y26", "name": "2026專員回報專區", "mimeType": FOLDER, "parent": "ROOT"},
        {"id": "y27", "name": "2027專員回報專區", "mimeType": FOLDER, "parent": "ROOT"},
        {"id": "tp26", "name": "2026台北內勤工作表單", "mimeType": SHEET, "parent": "y26"},
        {"id": "tp27", "name": "2027台北內勤工作表單", "mimeType": SHEET, "parent": "y27"},
        {"id": "r27", "name": "2027專員名冊薪資", "mimeType": FOLDER, "parent": "R2"},
        {"id": "ro27", "name": "2027專員名冊與時數-台北", "mimeType": SHEET, "parent": "r27"},
        {"id": "sa27", "name": "2027專員薪資相關-台北", "mimeType": SHEET, "parent": "r27"},
        {"id": "f27", "name": "2027年", "mimeType": FOLDER, "parent": "FIN"},
        {"id": "tc27", "name": "台中2027財報", "mimeType": SHEET, "parent": "f27"},
        {"id": "d27", "name": "2027", "mimeType": FOLDER, "parent": "DC"},
        {"id": "dcs", "name": "2027年終大掃除系統調整", "mimeType": SHEET, "parent": "d27"},
        {"id": "dcs_copy", "name": "2027年終大掃除系統調整 的副本", "mimeType": SHEET, "parent": "d27"},
        {"id": "dcv", "name": "2027年終大掃除VIP", "mimeType": SHEET, "parent": "d27"},
    ])


def _roots(mode="比對", since="2026"):
    written = {}
    drive = _drive()
    roots = YearRoots(_values(mode, since), lambda: drive, lambda row, text: written.__setitem__(row, text))
    return roots, written, drive


def test_parse_and_resolve_paths():
    rules = parse_rules(_values())
    drive = _drive()
    assert resolve(drive, find_rule(rules, "內勤工作表單", "台北"), 2027, "台北")[0] == "tp27"
    assert resolve(drive, find_rule(rules, "名冊薪資", "台北"), 2027, "台北", file_index=1)[0] == "sa27"
    assert resolve(drive, find_rule(rules, "財報", "台中"), 2027, "台中")[0] == "tc27"
    # 固定檔：根目錄 ID 就是檔案
    assert resolve(drive, find_rule(rules, "行銷費用總管理", "台北"), 2027)[0] == "MKT"
    # 同資料夾有「的副本」時只認完全同名
    assert resolve(drive, find_rule(rules, "大掃除", "共用"), 2027, file_index=0)[0] == "dcs"
    assert resolve(drive, find_rule(rules, "大掃除", "共用"), 2027, file_index=1)[0] == "dcv"


def test_original_mode_never_touches_drive():
    roots, written, drive = _roots("原設定")
    assert roots.choose("內勤工作表單", "台北", 2027, "OLD") == "OLD"
    assert drive.list_calls == 0 and not written


def test_compare_mode_keeps_configured_and_reports():
    roots, written, _ = _roots("比對")
    assert roots.choose("內勤工作表單", "台北", 2027, "tp26") == "tp26"
    assert "不一致" in written[4] and "tp27" in written[4]
    assert roots.choose("內勤工作表單", "台北", 2027, "tp27") == "tp27"
    assert written[4].endswith("一致")


def test_root_mode_uses_found_file_and_falls_back():
    roots, written, _ = _roots("根目錄")
    assert roots.choose("內勤工作表單", "台北", 2027, "tp26") == "tp27"
    assert roots.choose("內勤工作表單", "台北", 2028, "tp26") == "tp26"
    assert "找不到" in written[4] and "原設定" in written[4]


def test_before_since_year_uses_configured():
    roots, written, drive = _roots("根目錄", since="2027")
    assert roots.choose("內勤工作表單", "台北", 2026, "tp26") == "tp26"
    assert drive.list_calls == 0


def test_resolve_is_cached():
    roots, _, drive = _roots("比對")
    roots.choose("內勤工作表單", "台北", 2027, "tp26")
    calls = drive.list_calls
    roots.choose("內勤工作表單", "台北", 2027, "tp26")
    assert drive.list_calls == calls


def test_unknown_category_returns_configured():
    roots, _, _ = _roots("根目錄")
    assert roots.choose("不存在", "台北", 2027, "X") == "X"


def test_holiday_calendar_parse_and_fallback(monkeypatch):
    rows = [
        {"date": "20270101", "isHoliday": True},   # 週五元旦 → 假日
        {"date": "20270102", "isHoliday": True},   # 週六
        {"date": "20270104", "isHoliday": False},
        {"date": "20270220", "isHoliday": False},  # 週六補班
    ]
    holidays, makeup = tw_holidays.parse_calendar(rows)
    assert holidays == {date(2027, 1, 1)} and makeup == {date(2027, 2, 20)}

    tw_holidays._CACHE.clear()
    monkeypatch.setattr(tw_holidays, "_fetch", lambda year, timeout=10: rows if year == 2027 else None)
    assert not tw_holidays.is_workday(date(2027, 1, 1))
    assert tw_holidays.is_workday(date(2027, 1, 4))
    assert not tw_holidays.is_workday(date(2027, 2, 20))  # 補班日維持不算工作日
    # 抓不到 2026 日曆時用內建清單
    assert not tw_holidays.is_workday(date(2026, 10, 9), fallback=[date(2026, 10, 9)])
    assert tw_holidays.is_workday(date(2026, 10, 8), fallback=[date(2026, 10, 9)])


def _real(monkeypatch, name):
    """其他測試可能在 sys.modules 放了假模組，這裡確保載入真正的模組。"""
    import importlib
    import sys

    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def test_payment_registry_routes_categories(monkeypatch):
    reg = _real(monkeypatch, "tools.bank_statement.internal_payment_registry")

    rows = [
        {"類型": "清潔異動設定", "地區": "台北", "試算表ID": "A", "GID": "1"},
        {"類型": "清潔異動設定", "地區": "桃園", "試算表ID": "B", "GID": "2"},
        {"類型": "工具包押金退款", "地區": "台中", "試算表ID": "C", "GID": "3"},
    ]
    calls = []
    monkeypatch.setattr(reg, "read_sheet_records", lambda *a, **k: rows)
    monkeypatch.setattr(reg, "get_master_spreadsheet_id", lambda: "master")
    monkeypatch.setattr(reg, "choose_file_id",
                        lambda cat, area, cid, file_index=0: calls.append((cat, area, cid, file_index)) or cid + "!")
    assert reg.find_registry_row("清潔異動設定", "台北")["試算表ID"] == "A!"
    reg.find_registry_row("清潔異動設定", "桃園")
    reg.find_registry_row("工具包押金退款", "台中")
    assert calls == [("內勤工作表單", "台北", "A", 0), ("服務異動", "桃園", "B", 0), ("名冊薪資", "台中", "C", 1)]


def test_change_order_finds_year_file_by_root_only(monkeypatch):
    """服務異動以 orders-system 版為準：只依年度根目錄找檔，不看 secrets 或「清潔異動設定」的 ID。"""
    change_order = _real(monkeypatch, "tools.memo_system.change_order")
    from tools.memo_system import host_config
    import year_roots

    monkeypatch.setattr(change_order, "_secret_value", lambda key, default="": "SECRET")
    monkeypatch.setattr(host_config, "_change_cache", {
        "台北": {"spreadsheet_id": "TP", "gid": 1}, "桃園": {"spreadsheet_id": "TY", "gid": 2}})
    calls = []
    monkeypatch.setattr(year_roots, "root_file_id",
                        lambda cat, area, **k: calls.append((cat, area)) or f"ROOT-{area}")
    opened = []

    class Client:
        def open_by_key(self, key):
            opened.append(key)
            raise RuntimeError("stop")

    monkeypatch.setattr(change_order, "_get_gspread_client", lambda: Client())
    for region in ("台北", "桃園"):
        try:
            change_order.get_worksheet(region)
        except RuntimeError:
            pass
    assert calls == [("內勤工作表單", "台北"), ("服務異動", "桃園")]
    assert opened == ["ROOT-台北", "ROOT-桃園"]
    assert change_order.supported_regions() == ["台北", "桃園"]


def test_l_column_keeps_one_line_per_area_and_row_status():
    from tools.common.year_roots import row_status

    values = _values("比對")
    values[5][1] = "台北／台中"  # 財報列（第 6 列）兩區
    drive = _drive()
    drive.items.append({"id": "f26", "name": "2026年", "mimeType": FOLDER, "parent": "FIN"})
    written = {}
    roots = YearRoots(values, lambda: drive, lambda row, text: written.__setitem__(row, text))
    roots.choose("財報", "台中", 2027, "tc27")
    roots.choose("財報", "台北", 2027, "x")
    lines = written[6].splitlines()
    assert len(lines) == 2 and any("台中 2027 一致" in l for l in lines) and any("台北 2027 找不到" in l for l in lines)
    rule = [r for r in roots.rules if r.row == 6][0]
    assert row_status(rule) == "有不一致"
    # 重新讀設定表時，L 欄每行會被解析回來
    values[5] += [""] * (12 - len(values[5]))
    values[5][11] = written[6]
    again = parse_rules(values)
    assert set([r for r in again if r.row == 6][0].results) == {"台中 2027", "台北 2027"}


def test_set_modes_only_switches_consistent_compare_rows():
    from tools.common.year_roots import set_modes

    values = _values("比對")
    values[3] += [""] * (12 - len(values[3]))
    values[3][11] = "2026/10/17 08:00 台北 2027 一致"
    values[4] += [""] * (12 - len(values[4]))
    values[4][11] = "2026/10/17 08:00 台北 2027 不一致：…"
    roots = YearRoots(values, lambda: None)

    class Sheets:
        data = None

        def spreadsheets(self):
            return self

        def values(self):
            return self

        def batchUpdate(self, spreadsheetId, body):
            Sheets.data = body["data"]
            return type("R", (), {"execute": lambda self: {}})()

    assert set_modes(Sheets(), "m", roots, "根目錄", only_consistent=True) == [4]
    assert Sheets.data == [{"range": "'年度根目錄設定'!K4", "values": [["根目錄"]]}]
    assert set_modes(Sheets(), "m", roots, "原設定") == [4, 5, 6, 7, 8]


def test_check_year_lists_each_file():
    from tools.common.year_roots import check_year

    roots, _, _ = _roots("比對")
    rows = check_year(roots, 2027)
    by = {(r["類別"], r["地區"], r["檔名規則"]): r for r in rows}
    assert by[("內勤工作表單", "台北", "{YYYY}台北內勤工作表單")]["檔案ID"] == "tp27"
    assert by[("名冊薪資", "台北", "{YYYY}專員薪資相關-台北")]["檔案ID"] == "sa27"
    assert by[("財報", "台北", "{地區}{YYYY}財報")]["結果"].startswith("找不到")
    assert by[("大掃除", "共用", "{YYYY}年終大掃除VIP")]["檔案ID"] == "dcv"


def test_shortcut_follows_target_and_home_appliance_alias():
    """捷徑跟到目標檔；家電區財報先找「家電YYYY財報」，2026 年找不到時用舊稱「電器2026財報」。"""
    from tools.common.year_roots import check_year

    shortcut = "application/vnd.google-apps.shortcut"
    values = HEADER + [
        ["財報", "家電", "FIN", "02", "{YYYY}年", "—", "{地區}{YYYY}財報", "2026", "", "", "比對"],
        ["承攬費mail", "桃園", "SV", "承攬費", "{YYYY}專員承攬服務費", "03.桃園專員",
         "{YYYY}承攬服務費mail-桃園", "2026", "", "", "比對"],
        ["儲值金", "各地區", "SV2", "儲值金", "依期別自動建立", "—", "{YYYYMM}儲值金結算", "2026", "", "", "比對"],
    ]
    drive = FakeDrive([
        {"id": "f26", "name": "2026年", "mimeType": FOLDER, "parent": "FIN"},
        {"id": "el26", "name": "電器2026財報", "mimeType": SHEET, "parent": "f26"},
        {"id": "f27", "name": "2027年", "mimeType": FOLDER, "parent": "FIN"},
        {"id": "hm27", "name": "家電2027財報", "mimeType": SHEET, "parent": "f27"},
        {"id": "s26", "name": "2026專員承攬服務費", "mimeType": FOLDER, "parent": "SV"},
        {"id": "ty", "name": "03.桃園專員", "mimeType": FOLDER, "parent": "s26"},
        {"id": "sc", "name": "2026承攬服務費mail_桃園", "mimeType": shortcut, "parent": "ty",
         "shortcutDetails": {"targetId": "mail26", "targetMimeType": SHEET}},
    ])
    rules = parse_rules(values)
    assert resolve(drive, find_rule(rules, "財報", "家電"), 2026, "家電") == ("el26", "2026年/電器2026財報")
    assert resolve(drive, find_rule(rules, "財報", "家電"), 2027, "家電")[0] == "hm27"
    assert resolve(drive, find_rule(rules, "承攬費mail", "桃園"), 2026, "桃園")[0] == "mail26"
    rows = check_year(YearRoots(values, lambda: drive), 2026)
    assert [r["結果"] for r in rows if r["類別"] == "儲值金"] == ["依期別自動建立（不需檢查）"]


def test_write_check_column_one_cell_per_row():
    from tools.common.year_roots import CHECK_COLUMN, write_check_column

    class Sheets:
        body = None

        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, **_):
            return _Req({"sheets": [{"properties": {"title": "年度根目錄設定", "sheetId": 5,
                                                    "gridProperties": {"columnCount": 12}}}]})

        def batchUpdate(self, spreadsheetId, body):
            if "requests" in body:
                Sheets.appended = body["requests"][0]["appendDimension"]["length"]
            else:
                Sheets.body = body
            return _Req({})

    rows = [{"列": "16", "地區": "台北", "結果": "找到", "路徑": "2026專員回報專區/2026台北內勤工作表單"},
            {"列": "31", "地區": "台北", "結果": "找到", "路徑": "2026薪資/台北/2026台北薪資單總表"},
            {"列": "31", "地區": "桃園", "結果": "找不到：找不到「x」", "路徑": ""}]
    write_check_column(Sheets(), "master", 2026, rows)
    cells = {d["range"]: d["values"][0][0] for d in Sheets.body["data"]}
    assert f"'年度根目錄設定'!{CHECK_COLUMN}3" in cells
    row31 = cells[f"'年度根目錄設定'!{CHECK_COLUMN}31"].split("\n")
    assert Sheets.appended == 1
    assert row31[1:] == ["2026 台北 找到：2026薪資/台北/2026台北薪資單總表", "2026 桃園 找不到：找不到「x」"]
