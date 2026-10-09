import re

from tools.annual_rollover.new_year_generate import (
    filter_spec_by_area, FOLDER_MIME, SHEET_MIME, Rollover, parse_row, parse_rows_arg, strip_year,
)


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    def __init__(self, items):
        self.items = {i["id"]: dict(i) for i in items}
        self.counter = 0

    def files(self):
        return self

    def list(self, q, **_):
        if q.startswith("name = "):
            name = q.split("'")[1]
            return _Req(lambda: {"files": [i for i in self.items.values() if i["name"] == name]})
        parent = q.split("'")[1]
        return _Req(lambda: {"files": [i for i in self.items.values() if i["parent"] == parent]})

    def _new(self, name, mime, parent):
        self.counter += 1
        item = {"id": f"new{self.counter}", "name": name, "mimeType": mime, "parent": parent}
        self.items[item["id"]] = item
        return item

    def create(self, body, **_):
        def run():
            item = self._new(body["name"], body["mimeType"], body["parents"][0])
            if "shortcutDetails" in body:
                item["shortcutDetails"] = body["shortcutDetails"]
            return item
        return _Req(run)

    def get(self, fileId, **_):
        item = self.items[fileId]
        return _Req(lambda: {**item, "parents": [item["parent"]]})

    def copy(self, fileId, body, **_):
        mime = self.items[fileId]["mimeType"]
        return _Req(lambda: self._new(body["name"], mime, body["parents"][0]))

    def update(self, fileId, body, **_):
        def run():
            self.items[fileId]["name"] = body["name"]
            return self.items[fileId]
        return _Req(run)


class FakeSheets:
    def __init__(self, tables):
        self.tables = tables
        self.writes = {}
        self.cleared = []
        self.requests = []
        self.titles = None
        self.appended = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, fields=None, **_):
        if fields:
            titles = self.titles or ["富邦更新", "請款記錄", "零用金", "科目對照表", "股東損益表_財務", "台北財報",
                                     "2026目標", "信用卡", "ATM", "清潔異動"]
            return _Req(lambda: {"sheets": [{"properties": {"title": t, "sheetId": i}}
                                            for i, t in enumerate(titles)]})
        sheet = range.split("!")[0].strip("'")
        return _Req(lambda: {"values": self.tables.get(sheet, [])})

    def update(self, spreadsheetId, range, valueInputOption, body):
        return _Req(lambda: self.writes.__setitem__(range, body["values"]))

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.appended_to = getattr(self, "appended_to", [])
        self.appended_to.append(spreadsheetId)
        return _Req(lambda: self.appended.extend(body["values"]) if spreadsheetId == "master" else None)

    def batchClear(self, spreadsheetId, body):
        return _Req(lambda: self.cleared.extend((spreadsheetId, r) for r in body["ranges"]))

    def batchUpdate(self, spreadsheetId, body):
        def run():
            if "requests" in body:
                self.requests.extend((spreadsheetId, r) for r in body["requests"])
                return
            for d in body["data"]:
                self.writes[(spreadsheetId, d["range"])] = d["values"]
        return _Req(run)


def _setup():
    drive = FakeDrive([
        {"id": "y2026", "name": "2026年", "mimeType": FOLDER_MIME, "parent": "ROOTFOLDERID_0123456789"},
        {"id": "tp", "name": "台北2026財報", "mimeType": SHEET_MIME, "parent": "y2026"},
        {"id": "rv", "name": "2026目標及review", "mimeType": SHEET_MIME, "parent": "y2026"},
    ])
    sheets = FakeSheets({
        "生成新年度": [
            ["", "資料夾", "", "前一年度資料夾範例", "檔案", "", "", "工作表整理"],
            ["財務報表", "https://drive.google.com/drive/folders/ROOTFOLDERID_0123456789", "",
             "", "台北2026財報，2026目標及review", "", "", "移除富邦更新/元大更新的A2:"],
        ],
        "新年度ID": [["台北", "2026台北財報／年度工作檔"], ["共用", "Jenny's Lemonhometools"]],
    })
    return drive, sheets


def test_parse_helpers():
    spec = parse_row(3, ["x", "https://drive.google.com/drive/u/0/folders/ABCDEFGHIJKLMNOPQRSTU", "",
                         "2026/台北/桃園", "2026a-台北"], 2026)
    assert spec.year_folder_names == ["2026"]
    assert spec.sub_folder_names == ["台北", "桃園"]
    assert parse_rows_arg("2-4,7", 10) == [2, 3, 4, 7]
    assert strip_year("2026台北財報") == "台北財報" == strip_year("台北2026財報")
    assert strip_year("2026家電財報") == strip_year("電器2026財報")
    assert strip_year("2026目標及review檔") == strip_year("2026目標及review")


def test_generate_copies_records_and_is_idempotent():
    drive, sheets = _setup()
    records = Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    names = {i["name"]: i for i in drive.items.values()}
    assert names["2027年"]["parent"] == "ROOTFOLDERID_0123456789"
    assert names["台北2027財報"]["parent"] == names["2027年"]["id"]
    assert names["2027目標及review"]["parent"] == names["2027年"]["id"]
    assert not [k for k in sheets.writes if "'新年度ID'" in str(k)]  # 不再寫「新年度ID」
    tp = names["台北2027財報"]["id"]
    rv = names["2027目標及review"]["id"]
    assert (tp, "'富邦更新'!A2:H") in sheets.cleared
    assert (tp, "'請款記錄'!A2:J") in sheets.cleared
    assert (tp, "'元大更新'!A2:I") not in sheets.cleared  # 沒有這個分頁就略過
    assert (tp, "'零用金'!A3:H") in sheets.cleared
    assert sheets.writes[(tp, "'科目對照表'!E1")] == [[2027]]
    assert sheets.writes[(tp, "'股東損益表_財務'!A241")] == [
        ['=IMPORTRANGE("tp","股東損益表_財務!$a$1:$aa200")']]
    assert sheets.writes[(rv, "'台北財報'!A1")] == [
        [f'=IMPORTRANGE("{tp}","股東損益表_財務!$A$1:$z$500")']]
    assert len(records) == 3

    before, cleared = len(drive.items), len(sheets.cleared)
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    assert len(drive.items) == before
    assert len(sheets.cleared) == cleared  # 沿用的檔案不再清除


def test_fresh_renames_existing_instead_of_deleting():
    drive, sheets = _setup()
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    Rollover(drive, sheets, "master", 2027, fresh=True, log=lambda *_: None).run("2")
    titles = [i["name"] for i in drive.items.values()]
    assert titles.count("2027年") == 1
    assert any(t.startswith("2027年_舊版") for t in titles)


def test_dry_run_writes_nothing():
    drive, sheets = _setup()
    before = len(drive.items)
    Rollover(drive, sheets, "master", 2027, dry_run=True, log=lambda *_: None).run("2")
    assert len(drive.items) == before and not sheets.writes


def test_run_filters_by_name():
    drive, sheets = _setup()
    roll = Rollover(drive, sheets, "master", 2027, dry_run=True, log=lambda *_: None)
    assert len(roll.run(names=["財務報表"])) == 3
    assert roll.run(names=["不存在"]) == []


def test_office_form_post_process():
    drive = FakeDrive([
        {"id": "f", "name": "2026專員回報表單", "mimeType": FOLDER_MIME, "parent": "OFFICEFOLDERID_0123456789"},
        {"id": "of", "name": "2026台北內勤工作表單", "mimeType": SHEET_MIME, "parent": "OFFICEFOLDERID_0123456789"},
        {"id": "fin27", "name": "台北2027財報", "mimeType": SHEET_MIME, "parent": "x"},
    ])
    sheets = FakeSheets({
        "生成新年度": [
            [],
            ["內勤表單", "https://drive.google.com/drive/folders/OFFICEFOLDERID_0123456789", "",
             "2026專員回報表單", "2026台北內勤工作表單", "台北"],
        ],
        "新年度ID": [],
    })
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    new_id = next(i["id"] for i in drive.items.values() if i["name"] == "2027台北內勤工作表單")
    assert (new_id, "'信用卡'!A2:J") in sheets.cleared
    reqs = [r for fid, r in sheets.requests if fid == new_id]
    assert {"updateSheetProperties": {"properties": {"sheetId": 6, "title": "2027目標"}, "fields": "title"}} in reqs
    deleted = {r["deleteSheet"]["sheetId"] for r in reqs if "deleteSheet" in r}
    assert deleted == {0, 1, 2, 3, 4, 5}  # 富邦更新…台北財報 不在保留清單
    fill = next(r["repeatCell"] for r in reqs if "repeatCell" in r)
    assert fill["range"] == {"sheetId": 9, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 31}
    atm = sheets.writes[(new_id, "'ATM'!A2")][0][0]
    assert atm.startswith('=filter({filter(importrange("fin27","富邦更新!$A2:$H")')
    assert atm.endswith('},{0,1,1,1,1,1,0,1})')


def test_filter_by_area():
    spec = parse_row(2, ["財務報表", "https://drive.google.com/drive/folders/ABCDEFGHIJKLMNOPQRSTU", "", "",
                         "台北2026財報，電器2026財報，2026目標及review"], 2026)
    assert filter_spec_by_area(spec, "全區").file_names == ["台北2026財報", "電器2026財報", "2026目標及review"]
    assert filter_spec_by_area(parse_row(2, ["x", "ABCDEFGHIJKLMNOPQRSTU", "", "", "台北2026財報，電器2026財報"], 2026),
                               "電器").file_names == ["電器2026財報"]
    assert filter_spec_by_area(parse_row(2, ["x", "ABCDEFGHIJKLMNOPQRSTU", "", "", "2026目標及review"], 2026),
                               "目標及review").file_names == ["2026目標及review"]
    office = parse_row(3, ["內勤表單", "ABCDEFGHIJKLMNOPQRSTU", "", "", "2026台中內勤工作表單", "台中"], 2026)
    assert filter_spec_by_area(office, "台北") is None
    sub = parse_row(4, ["服務分潤表", "ABCDEFGHIJKLMNOPQRSTU", "", "01.台北專員/06.電器專員"], 2026)
    assert filter_spec_by_area(sub, "電器").sub_folder_names == ["06.電器專員"]


def test_staff_post_process():
    drive = FakeDrive([
        {"id": "y", "name": "2026", "mimeType": FOLDER_MIME, "parent": "STAFFFOLDERID_0123456789"},
        {"id": "roster26", "name": "2026專員名冊與時數-台北", "mimeType": SHEET_MIME, "parent": "y"},
        {"id": "salary26", "name": "2026專員薪資相關-台北", "mimeType": SHEET_MIME, "parent": "y"},
    ])
    header = [""] * 17 + ["地區"] + [""] * 11 + ["地區"]
    sheets = FakeSheets({
        "生成新年度": [[], ["專員名冊/薪資檔", "https://drive.google.com/drive/folders/STAFFFOLDERID_0123456789",
                         "", "2026", "2026專員名冊與時數-台北，2026專員薪資相關-台北", "台北"]],
        "新年度ID": [],
        "2026排班統計表": [header],
        "工具包押金": [[""], ["2026/02/12不退"], [""], ["已匯款"]],  # G2:G → 第 3、5 列有值
    })
    sheets.titles = ["2026排班統計表", "面試紀錄", "202512專員名冊", "202610專員名冊",
                     "場次和時數", "工具包押金", "202609調薪資料", "202608調薪資料", "202603調薪資料-old"]
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    ids = {i["name"]: i["id"] for i in drive.items.values()}
    roster, salary = ids["2027專員名冊與時數-台北"], ids["2027專員薪資相關-台北"]

    assert (roster, "'2026排班統計表'!R5:Z") in sheets.cleared
    assert (roster, "'2026排班統計表'!AD5:AL") in sheets.cleared
    assert (roster, "'面試紀錄'!A2:K") in sheets.cleared
    r_reqs = [r for fid, r in sheets.requests if fid == roster]
    assert {"updateSheetProperties": {"properties": {"sheetId": 0, "title": "2027排班統計表"},
                                      "fields": "title"}} in r_reqs
    assert [r["deleteSheet"]["sheetId"] for r in r_reqs if "deleteSheet" in r] == [2]  # 202512 刪、202610 留

    assert (salary, "'場次和時數'!E2:AN") in sheets.cleared
    assert sheets.writes[(salary, "'場次和時數'!A2")] == [[2027]]
    s_reqs = [r for fid, r in sheets.requests if fid == salary]
    deleted_rows = [r["deleteDimension"]["range"]["startIndex"] for r in s_reqs if "deleteDimension" in r]
    assert deleted_rows == [4, 2]  # 第 5、3 列（0-based 4、2），由下往上
    assert [r["deleteSheet"]["sheetId"] for r in s_reqs if "deleteSheet" in r] == [7, 8]  # 202608、-old 刪
    assert {"findReplace": {"find": "roster26", "replacement": roster,
                            "allSheets": True, "includeFormulas": True}} in s_reqs


def _service_drive(extra=()):
    S = "application/vnd.google-apps.shortcut"
    return FakeDrive([
        {"id": "y", "name": "2026專員承攬服務費", "mimeType": FOLDER_MIME, "parent": "SERVICEFOLDERID_0123456789"},
        {"id": "ty", "name": "03.桃園專員", "mimeType": FOLDER_MIME, "parent": "y"},
        {"id": "hc", "name": "04.新竹專員", "mimeType": FOLDER_MIME, "parent": "y"},
        {"id": "mail", "name": "2026承攬服務費mail_桃園", "mimeType": SHEET_MIME, "parent": "ty"},
        {"id": "rev", "name": "2026桃園營業額總表", "mimeType": SHEET_MIME, "parent": "fin"},
        {"id": "rev_s", "name": "2026桃園營業額總表", "mimeType": S, "parent": "ty",
         "shortcutDetails": {"targetId": "rev"}},
        {"id": "pay", "name": "2026桃園專員薪資(外場) ", "mimeType": SHEET_MIME, "parent": "ty"},
        {"id": "xlsx", "name": "2026桃園營業額總表", "mimeType": "application/vnd.ms-excel", "parent": "ty"},
        {"id": "hc_out", "name": "新竹2026支出明細", "mimeType": SHEET_MIME, "parent": "hc"},
        *extra,
    ])


def _service_sheets():
    return FakeSheets({
        "生成新年度": [[], ["服務分潤表", "https://drive.google.com/drive/folders/SERVICEFOLDERID_0123456789",
                         "", "2026專員承攬服務費/03.桃園專員/04.新竹專員"]],
        "新年度ID": [],
    })


def test_service_files_copy_originals_and_cross_area():
    S = "application/vnd.google-apps.shortcut"
    drive, sheets = _service_drive(), _service_sheets()
    sheets.titles = ["mail", "202609-1", "202609-2", "202608-2工具包押金", "富邦ATM", "桃園零用金", "其他"]
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run(names=["服務分潤表"])
    items = list(drive.items.values())

    def one(name):
        found = [i for i in items if i["name"] == name]
        assert len(found) == 1, name
        return found[0]
    new_ty = next(i for i in items if i["name"] == "03.桃園專員" and i["id"] != "ty")
    new_hc = next(i for i in items if i["name"] == "04.新竹專員" and i["id"] != "hc")
    mail = one("2027承攬服務費mail-桃園")
    assert mail["parent"] == new_ty["id"]
    # mail 只留 mail 與最後一期
    deleted = {r["deleteSheet"]["sheetId"] for fid, r in sheets.requests if fid == mail["id"] and "deleteSheet" in r}
    assert deleted == {1, 3, 4, 5, 6}
    # 捷徑 → 用原始檔複製，直接放在新年度地區資料夾，不建捷徑
    assert one("2027營業額總表-桃園")["parent"] == new_ty["id"]
    assert not [i for i in items if i["mimeType"] == S and i["id"] != "rev_s"]
    # 專員名冊薪資改由「專員表單」生成
    assert not [i for i in items if i["name"] == "2027專員名冊薪資-桃園"]
    # 新竹有自己的支出明細 → 用新竹的；只留富邦ATM、零用金
    out = one("2027支出明細-新竹")
    assert out["parent"] == new_hc["id"]
    deleted = {r["deleteSheet"]["sheetId"] for fid, r in sheets.requests if fid == out["id"] and "deleteSheet" in r}
    assert deleted == {0, 1, 2, 3, 6}


def test_staff_function_generates_payroll_in_service_folder():
    drive = _service_drive([{"id": "exp27", "name": "2027支出明細-新竹", "mimeType": SHEET_MIME, "parent": "z"}])
    sheets = _service_sheets()
    sheets.titles = ["場次和時數", "專員請款", "2026薪資", "202609調薪資料", "202610調薪資料",
                     "202610專員名冊", "2026排班統計表", "富邦ATM"]
    sheets.tables["2026排班統計表"] = [["=A1", "5", ""], ["x", "=B2"]]
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run(names=["專員名冊/薪資檔"], area="新竹")
    items = list(drive.items.values())
    new = next(i for i in items if i["name"] == "2027專員名冊薪資-新竹")
    new_hc = next(i for i in items if i["name"] == "04.新竹專員" and i["id"] != "hc")
    assert new["parent"] == new_hc["id"]
    assert not [i for i in items if i["name"] == "2027專員名冊薪資-桃園"]  # 只跑新竹
    reqs = [r for fid, r in sheets.requests if fid == new["id"]]
    titles = {r["updateSheetProperties"]["properties"]["title"] for r in reqs if "updateSheetProperties" in r}
    assert titles == {"2027薪資", "2027排班統計表"}
    assert {r["deleteSheet"]["sheetId"] for r in reqs if "deleteSheet" in r} == {3}
    assert any("findReplace" in r and r["findReplace"]["sheetId"] == 2 for r in reqs)
    assert (new["id"], "'專員請款'!A3:J") in sheets.cleared
    assert (new["id"], "'202610調薪資料'!B3:M") in sheets.cleared
    assert sheets.writes[(new["id"], "'場次和時數'!A2")] == [["2027"]]
    assert sheets.writes[(new["id"], "'富邦ATM'!A1")] == [['=importrange("exp27","富邦ATM!A1:P")']]
    assert sheets.writes[(new["id"], "'2027排班統計表'!Y6:ET")] == [["=A1", "", ""], ["", "=B2"]]


def test_service_rule_names():
    from tools.annual_rollover.new_year_generate import SERVICE_FILE_RULES, dash_area

    def target_for(old, area):
        for rule in SERVICE_FILE_RULES:
            if area in rule["areas"] and any(
                    re.fullmatch(p.format(y=2026, a=area), dash_area(old)) for p in rule["sources"]):
                return rule["target"].format(Y=2027, a=area)
    assert target_for("2026高雄內勤工作表單", "高雄") == "2027內勤工作表單-高雄"
    assert target_for("2026內勤工作表單_高雄", "高雄") == "2027內勤工作表單-高雄"
    assert target_for("2026紙本 / 中獎發票-新竹", "新竹") == "2027紙本／中獎發票-新竹"
    assert target_for("2026承攬服務費mail_台北", "台北") == "2027承攬服務費mail-台北"


def test_dash_area():
    from tools.annual_rollover.new_year_generate import dash_area
    assert dash_area("2027專員名冊與時數_台北") == "2027專員名冊與時數-台北"
    assert dash_area("2027承攬服務費mail-高雄") == "2027承攬服務費mail-高雄"
    assert dash_area("2027台北內勤工作表單") == "2027台北內勤工作表單"


def test_strip_year_treats_dash_and_underscore_alike():
    assert strip_year("2026專員名冊與時數_台北") == strip_year("2026專員名冊與時數-台北")


def test_year_columns_and_invoice_folder_clone():
    from tools.annual_rollover.new_year_generate import FINANCE_YEAR_FOLDERS
    drive = FakeDrive([
        {"id": "root", "name": "台北財務", "mimeType": FOLDER_MIME, "parent": "top"},
        {"id": "f26", "name": "2026發票", "mimeType": FOLDER_MIME, "parent": "root"},
        {"id": "p", "name": "01-02", "mimeType": FOLDER_MIME, "parent": "f26"},
        {"id": "pf", "name": "202601紙本發票-台北.zip", "mimeType": "application/zip", "parent": "p"},
        {"id": "inv", "name": "2026紙本／中獎發票-台北", "mimeType": SHEET_MIME, "parent": "f26"},
        {"id": "dis", "name": "2026發票折讓單相關-台北", "mimeType": SHEET_MIME, "parent": "f26"},
    ])
    sheets = FakeSheets({"生成新年度": [], "新年度ID": [],
                         "2026財報總表": [["2026", "", "2026.01", "2026.02"]]})
    sheets.titles = ["2026", "中獎發票", "202601", "2026財報總表"]
    roll = Rollover(drive, sheets, "master", 2027, log=lambda *_: None)
    job = dict(FINANCE_YEAR_FOLDERS[0], folder_id="root")
    records = roll.clone_year_folder(job)
    names = {i["name"]: i for i in drive.items.values()}
    new_folder = names["2027發票"]
    assert new_folder["parent"] == "root"
    parents = sorted(i["parent"] for i in drive.items.values() if i["name"] == "01-02")
    assert parents == sorted(["f26", new_folder["id"]])
    assert not [i for i in drive.items.values() if i["name"].endswith(".zip") and i["id"] != "pf"]
    inv = names["2027紙本／中獎發票-台北"]
    assert names["2027發票折讓單相關-台北"]["parent"] == new_folder["id"]
    assert (inv["id"], "'2027'!A2:Y") in sheets.cleared and (inv["id"], "'中獎發票'!A2:L") in sheets.cleared
    reqs = [r for fid, r in sheets.requests if fid == inv["id"]]
    assert {r["deleteSheet"]["sheetId"] for r in reqs if "deleteSheet" in r} == {2}
    assert len(records) == 3

    # 營業額總表：前一年度 12 個月欄位右側插入新年度、前一年度貼上為值
    record = {"new_name": "x", "new_id": "rev"}
    roll.apply_ops(record, {"year_columns": "{y}財報總表"})
    batch = [r for fid, r in sheets.requests if fid == "rev"]
    assert batch[0]["insertDimension"]["range"]["startIndex"] == 4
    assert batch[2]["copyPaste"]["pasteType"] == "PASTE_VALUES"
    assert sheets.writes[("rev", "'2026財報總表'!E1:F1")] == [["2027.01", "2027.02"]]


def test_run_log():
    drive, sheets = _setup()
    roll = Rollover(drive, sheets, "master", 2027, log=lambda *_: None)
    records = roll.run(names=["財務報表"])
    roll.append_run_log("財務報表", "全區", records)
    assert sheets.appended[0][2:7] == ["財務報表", "全區", "執行", "", "摘要"]
    assert sheets.appended[0][11] == "成功"
    assert len(sheets.appended) == 1 + len(records)
    assert {row[11] for row in sheets.appended[1:]} <= {"新建", "沿用", ""}
    # 同一批 Log 也追加到 salary-system／orders-system 的「生成新年度Log」
    from tools.annual_rollover.new_year_generate import SYSTEM_LOG_SPREADSHEETS
    assert sheets.appended_to[-2:] == list(SYSTEM_LOG_SPREADSHEETS.values())
