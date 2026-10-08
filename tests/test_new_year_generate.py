from tools.annual_rollover.new_year_generate import (
    FOLDER_MIME, SHEET_MIME, Rollover, parse_row, parse_rows_arg, strip_year,
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
        parent = q.split("'")[1]
        return _Req(lambda: {"files": [i for i in self.items.values() if i["parent"] == parent]})

    def _new(self, name, mime, parent):
        self.counter += 1
        item = {"id": f"new{self.counter}", "name": name, "mimeType": mime, "parent": parent}
        self.items[item["id"]] = item
        return item

    def create(self, body, **_):
        return _Req(lambda: self._new(body["name"], body["mimeType"], body["parents"][0]))

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

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, fields=None):
        if fields:
            return _Req(lambda: {"sheets": [{"properties": {"title": "富邦更新"}}]})
        sheet = range.split("!")[0].strip("'")
        return _Req(lambda: {"values": self.tables.get(sheet, [])})

    def update(self, spreadsheetId, range, valueInputOption, body):
        return _Req(lambda: self.writes.__setitem__(range, body["values"]))

    def batchClear(self, spreadsheetId, body):
        return _Req(lambda: self.cleared.extend(body["ranges"]))


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


def test_generate_copies_records_and_is_idempotent():
    drive, sheets = _setup()
    records = Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    names = {i["name"]: i for i in drive.items.values()}
    assert names["2027年"]["parent"] == "ROOTFOLDERID_0123456789"
    assert names["台北2027財報"]["parent"] == names["2027年"]["id"]
    assert names["2027目標及review"]["parent"] == names["2027年"]["id"]
    assert sheets.writes["'新年度ID'!C1:D1"] == [[names["台北2027財報"]["id"], "台北2027財報"]]
    assert "'富邦更新'!A2:ZZZ" in sheets.cleared
    assert len(records) == 3

    before = len(drive.items)
    Rollover(drive, sheets, "master", 2027, log=lambda *_: None).run("2")
    assert len(drive.items) == before


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
