"""生成新年度：營業額總表捷徑、行銷費用總管理、清潔用品。"""
from tests.test_new_year_generate import FakeDrive, _Req
from tools.annual_rollover import new_year_generate as nyg
from tools.annual_rollover.new_year_generate import SHEET_MIME, SHORTCUT_MIME, Rollover


class Sheets:
    """記錄寫入的簡易 Sheets：tabs = {檔案ID: {分頁名稱: sheetId}}，cells = {(檔案, range): values}。"""

    def __init__(self, tabs, cells=None):
        self.tabs, self.cells = tabs, cells or {}
        self.cleared, self.requests, self.writes = [], [], {}

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, fields=None, **_):
        if range is None:
            return _Req(lambda: {"sheets": [
                {"properties": {"title": t, "sheetId": i, "gridProperties": {"rowCount": 10}}}
                for t, i in self.tabs[spreadsheetId].items()]})
        return _Req(lambda: {"values": self.cells.get((spreadsheetId, range), [])})

    def batchGet(self, spreadsheetId, ranges, **_):
        return _Req(lambda: {"valueRanges": [{"values": self.cells.get((spreadsheetId, r), [])} for r in ranges]})

    def batchClear(self, spreadsheetId, body):
        return _Req(lambda: self.cleared.extend((spreadsheetId, r) for r in body["ranges"]))

    def batchUpdate(self, spreadsheetId, body):
        def run():
            if "data" in body:
                for d in body["data"]:
                    self.writes[(spreadsheetId, d["range"])] = d["values"]
                return {}
            replies = []
            for r in body["requests"]:
                self.requests.append((spreadsheetId, r))
                if "duplicateSheet" in r:
                    new_id = 900 + len(self.tabs[spreadsheetId])
                    self.tabs[spreadsheetId][r["duplicateSheet"]["newSheetName"]] = new_id
                    replies.append({"duplicateSheet": {"properties": {"sheetId": new_id}}})
                else:
                    replies.append({})
            return {"replies": replies}
        return _Req(run)


def _roll(drive, sheets, dry_run=False):
    return Rollover(drive, sheets, "master", 2027, dry_run=dry_run, log=lambda *_: None)


def test_shortcut_created_once():
    drive = FakeDrive([{"id": "fin", "name": "02 營業額總表", "mimeType": nyg.FOLDER_MIME, "parent": "x"}])
    roll = _roll(drive, Sheets({}))
    target = {"id": "rev27", "name": "2027營業額總表-台北"}
    roll.ensure_shortcut("fin", "2027台北營業額總表", target)
    roll.ensure_shortcut("fin", "2027台北營業額總表", target)
    shortcuts = [i for i in drive.items.values() if i["mimeType"] == SHORTCUT_MIME]
    assert len(shortcuts) == 1
    assert shortcuts[0]["shortcutDetails"] == {"targetId": "rev27"} and shortcuts[0]["parent"] == "fin"


def test_marketing_copies_and_clears_numbers_only():
    fid = nyg.MARKETING_FILE_ID
    sheets = Sheets({fid: {"2026廣告": 1, "2026總表": 2}}, {
        (fid, "'2027廣告'!A:E"): [["", "Google", "", "", "2026"], ["台北", "Google", "居家", "(SEM)", "台北居家(SEM)"],
                                  ["台北", "Google", "", "", "其他"]],
        (fid, "'2027廣告'!1:1"): [["", "Google", "", "", "2026", "=$E1&\".01\""]],
        (fid, "'2027總表'!1:1"): [[2026, "=$A1&\".01\""]],
        (fid, "'2027總表'!A:A"): [[2026], ["費用別"]] + [[""]] * 37 + [[2026]],
        (fid, "'2027廣告'!F2"): [[100]],
        (fid, "'2027廣告'!I2"): [["=SUM(A1)"]],
        (fid, "'2027總表'!C24:CH29"): [[5, "=A1", "=87369", "=1200+300"]],
    })
    records = _roll(FakeDrive([]), sheets).run_marketing()
    assert [r["new_name"] for r in records] == ["2027廣告", "2027總表"] and all(r["created"] for r in records)
    dup = [r["duplicateSheet"]["newSheetName"] for _, r in sheets.requests if "duplicateSheet" in r]
    assert dup == ["2027廣告", "2027總表"]
    finds = {(r["findReplace"]["find"], r["findReplace"]["replacement"])
             for _, r in sheets.requests if "findReplace" in r}
    assert ("2026廣告", "2027廣告") in finds and ("2026總表", "2027總表") in finds
    assert sheets.writes[(fid, "'2027廣告'!F2")] == [[""]]          # 數字清空
    assert (fid, "'2027廣告'!I2") not in sheets.writes               # 公式保留
    assert sheets.writes[(fid, "'2027總表'!C24:CH29")] == [["", "=A1", "", ""]]   # 只有數字的公式也清
    assert sheets.writes[(fid, "'2027廣告'!E1")] == [[2027]]           # 年度標題改新年度
    assert sheets.writes[(fid, "'2027總表'!A1")] == [[2027]]
    assert sheets.writes[(fid, "'2027總表'!A40")] == [[2027]]
    assert (fid, "'2027廣告'!F3") not in sheets.writes               # 「其他」列不動

    # 重跑：已存在就沿用，不再複製或清空
    sheets.requests.clear(); sheets.writes.clear()
    again = _roll(FakeDrive([]), sheets).run_marketing()
    assert not any(r["created"] for r in again) and not sheets.requests and not sheets.writes

    # 重新生成：既有工作表改名為舊版後重新複製、清空
    roll = _roll(FakeDrive([]), sheets)
    roll.fresh = True
    fresh = roll.run_marketing()
    assert all(r["created"] for r in fresh)
    renamed = [r["updateSheetProperties"]["properties"]["title"] for _, r in sheets.requests
               if "updateSheetProperties" in r]
    assert len(renamed) == 2 and all("_舊版" in t for t in renamed)
    assert sheets.writes[(fid, "'2027廣告'!F2")] == [[""]]


def test_supply_copy_and_clear_every_stock_sheet():
    drive = FakeDrive([{"id": "s26", "name": "2026清潔用品訂購記錄-四店共用new", "mimeType": SHEET_MIME,
                        "parent": nyg.SUPPLY_FOLDER_ID}])
    tc, tp = "庫存統計表-台中", "庫存統計表-台北"
    sheets = Sheets({"new1": {"進貨明細表-台北": 1, "進貨明細表-台中": 2, tc: 3, tp: 4, "收納庫存統計表-台北": 5}}, {
        ("new1", f"'{tc}'!B:B"): [[""], ["期末庫存"], ["期初庫存"], ["當月進貨"], ["期初庫存"]],
        ("new1", f"'{tp}'!B:B"): [[""], ["期末庫存"], ["期初庫存"]],
        ("new1", f"'{tc}'!C2:AY2"): [[1, 2]],
        ("new1", f"'{tc}'!C3:AY3"): [[1, "=C2"]],
        ("new1", f"'{tc}'!C5:AY5"): [[7, "=8"]],
        ("new1", f"'{tp}'!C3:AY3"): [[9]],
        ("new1", f"'{tc}'!A:A"): [["2026"], ["2025.12"], ["2026.01"], [""], ["=A$1&\".02\""], ["備註"]],
    })
    records = _roll(drive, sheets).run_supply()
    assert records[0]["new_name"] == "2027清潔用品訂購記錄-四店共用new" and records[0]["created"]
    cleared = {r for _, r in sheets.cleared}
    assert "'進貨明細表-台北'!G3:K" in cleared and "'進貨明細表-台中'!O3:T" in cleared
    false_cols = [r["repeatCell"] for _, r in sheets.requests if "repeatCell" in r]
    assert len(false_cols) == 2 and false_cols[0]["cell"]["userEnteredValue"] == {"boolValue": False}
    w = {k[1]: v for k, v in sheets.writes.items()}
    assert w[f"'{tc}'!C2:AY2"] == [["", ""]]                    # 第 2 列清空，不帶入前一年度
    assert w[f"'{tc}'!C3:AY3"] == [["", "=C2"]]                 # 期初庫存數字清空，公式保留
    assert w[f"'{tc}'!C5:AY5"] == [["", ""]]
    assert w[f"'{tp}'!C3:AY3"] == [[""]]                        # 台北也清
    assert f"'{tc}'!C4:AY4" not in w                            # 當月進貨不動
    assert not any(k.startswith("'收納庫存統計表") for k in w)
    labels = {k: v for k, v in w.items() if k.startswith(f"'{tc}'!A")}
    assert labels == {f"'{tc}'!A1": [["2027"]], f"'{tc}'!A2": [["2026.12"]], f"'{tc}'!A3": [["2027.01"]]}

    # 重跑不勾重新生成：已存在就沿用、不清空
    sheets.cleared.clear(); sheets.writes.clear()
    again = _roll(drive, sheets).run_supply()
    assert not again[0]["created"] and not sheets.cleared and not sheets.writes

    # 勾重新生成：既有檔改名為舊版保留，重新複製並清空
    sheets.tabs["new2"] = sheets.tabs["new1"]
    roll = _roll(drive, sheets)
    roll.fresh = True
    fresh = roll.run_supply()
    assert fresh[0]["created"] and fresh[0]["new_id"] == "new2"
    assert "_舊版" in drive.items["new1"]["name"] and not drive.items["new1"].get("trashed")
    assert ("new2", "'進貨明細表-台北'!G3:K") in sheets.cleared


def test_run_extra_functions_skip_sheet_rows(monkeypatch):
    roll = _roll(FakeDrive([]), Sheets({}))
    called = []
    monkeypatch.setattr(roll, "_run_rows", lambda *a, **k: called.append("rows") or [])
    monkeypatch.setattr(roll, "run_supply", lambda: [{"kind": "檔案", "new_name": "x", "new_id": "1"}])
    records = roll.run(names=["清潔用品"])
    assert records == [{"kind": "檔案", "new_name": "x", "new_id": "1"}] and called == []


def test_service_revenue_file_gets_finance_shortcut(monkeypatch):
    fin_tp = nyg.SERVICE_FILE_RULES[1]["shortcut"]["folders"]["台北"]
    drive = FakeDrive([
        {"id": "old", "name": "01.台北專員", "mimeType": nyg.FOLDER_MIME, "parent": "y26"},
        {"id": "new", "name": "01.台北專員", "mimeType": nyg.FOLDER_MIME, "parent": "y27"},
        {"id": "rev26", "name": "2026台北營業額總表", "mimeType": SHEET_MIME, "parent": "old"},
        {"id": fin_tp, "name": "02 營業額總表", "mimeType": nyg.FOLDER_MIME, "parent": "x"},
    ])
    roll = _roll(drive, Sheets({}))
    monkeypatch.setattr(roll, "apply_ops", lambda *a, **k: None)
    records = roll.copy_service_files(drive.items["old"], drive.items["new"], func="服務分潤表")
    rev = next(r for r in records if r["new_name"] == "2027營業額總表-台北")
    shortcut = next(i for i in drive.items.values() if i["mimeType"] == SHORTCUT_MIME)
    assert shortcut["name"] == "2027台北營業額總表" and shortcut["parent"] == fin_tp
    assert shortcut["shortcutDetails"]["targetId"] == rev["new_id"]


def test_marketing_location_switches_to_current_year_sheet(monkeypatch):
    from tools.finance_management import statement_registry as reg

    class Svc:
        def __init__(self, titles):
            self.titles = titles

        def spreadsheets(self):
            return self

        def get(self, **_):
            return _Req(lambda: {"sheets": [{"properties": {"title": t}} for t in self.titles]})

    import datetime as dt

    real = dt.datetime

    class Now:
        @staticmethod
        def now(tz=None):
            return real(2027, 1, 5, tzinfo=tz)

    monkeypatch.setattr(dt, "datetime", Now)
    assert reg._current_year_title(Svc(["2026總表", "2027總表"]), "f", "2026總表") == "2027總表"
    assert reg._current_year_title(Svc(["2026總表"]), "f", "2026總表") == "2026總表"
    assert reg._current_year_title(Svc(["2026總表"]), "f", "台北") == "台北"
