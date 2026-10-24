"""生成新年度：春節禮品名單。"""
from tests.test_new_year_generate import FakeDrive, _Req
from tools.annual_rollover import new_year_generate as nyg
import pytest

from tools.annual_rollover.new_year_generate import (
    FOLDER_MIME, SHEET_MIME, Rollover, gift_band_counts, gift_band_labels, gift_headers,
    gift_list_formula, gift_pivot, gift_stats_table,
)
from tools.annual_rollover.tw_zipcodes import TW_ZIPCODES

ROOT = nyg.GIFT_ROOT_FOLDER_ID


class GiftDrive(FakeDrive):
    sheets = None

    def copy(self, fileId, body, **_):  # 帶 mimeType＝轉檔；沒帶 parents 就放在原資料夾；分頁一併複製
        src = self.items[fileId]

        def run():
            item = self._new(body["name"], body.get("mimeType", src["mimeType"]),
                             (body.get("parents") or [src["parent"]])[0])
            if self.sheets and fileId in self.sheets.tabs:
                self.sheets.tabs[item["id"]] = dict(self.sheets.tabs[fileId])
            return item
        return _Req(run)


class GiftSheets:
    """tabs = {檔案: [分頁名稱…]}；reads = {(檔案, range): values}；記錄 requests／writes／cleared。"""

    def __init__(self, tabs, reads=None):
        self.tabs = {f: {t: i for i, t in enumerate(ts)} for f, ts in tabs.items()}
        self.reads = reads or {}
        self.requests, self.writes, self.cleared = [], {}, []
        self.next_id = 100

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, **_):
        if range is None:
            tabs = self.tabs.setdefault(spreadsheetId, {"工作表1": 0})
            return _Req(lambda: {"sheets": [
                {"properties": {"title": t, "sheetId": i, "index": n,
                                "gridProperties": {"rowCount": 30, "columnCount": 20}}}
                for n, (t, i) in enumerate(tabs.items())]})
        return _Req(lambda: {"values": self.reads.get((spreadsheetId, range), [])})

    def batchGet(self, spreadsheetId, ranges, **_):
        return _Req(lambda: {"valueRanges": [{"values": self.reads.get((spreadsheetId, r), [])} for r in ranges]})

    def update(self, spreadsheetId, range, body, **_):
        return _Req(lambda: self.writes.__setitem__((spreadsheetId, range), body["values"]))

    def batchClear(self, spreadsheetId, body):
        return _Req(lambda: self.cleared.extend((spreadsheetId, r) for r in body["ranges"]))

    def batchUpdate(self, spreadsheetId, body):
        def run():
            if "data" in body:
                for d in body["data"]:
                    self.writes[(spreadsheetId, d["range"])] = d["values"]
                return {}
            tabs = self.tabs.setdefault(spreadsheetId, {"工作表1": 0})
            replies = []
            for r in body["requests"]:
                self.requests.append((spreadsheetId, r))
                reply = {}
                if "addSheet" in r or "duplicateSheet" in r:
                    title = r["addSheet"]["properties"]["title"] if "addSheet" in r \
                        else r["duplicateSheet"]["newSheetName"]
                    self.next_id += 1
                    tabs[title] = self.next_id
                    reply = {next(iter(r)): {"properties": {"title": title, "sheetId": self.next_id}}}
                elif "deleteSheet" in r:
                    sid = r["deleteSheet"]["sheetId"]
                    tabs.pop(next(t for t, i in tabs.items() if i == sid))
                elif "updateSheetProperties" in r and "title" in r["updateSheetProperties"]["properties"]:
                    p = r["updateSheetProperties"]["properties"]
                    old = next(t for t, i in tabs.items() if i == p["sheetId"])
                    tabs[p["title"]] = tabs.pop(old)
                replies.append(reply)
            return {"replies": replies}
        return _Req(run)


SETTLE_HEADER = ["mv_id", "客戶姓名", "email", "電話", "地址", "LINE@", "購買日期", "購買項目", "購買原價", "服務金額",
                 "移轉訂單編號", "移轉剩餘時數", "移轉剩餘金額", "剩餘儲值金", "剩餘購物金", "會員等級",
                 "最後一次消費時間", "最後一次消費訂單", "最後一次服務時間", "VIP時數備註"]


def _settle_row(name, phone, address, line, date, plan, price):
    return ["1", name, "a@b", phone, address, line, date, plan, price] + [""] * 11


def _setup(threshold=""):
    drive = GiftDrive([
        {"id": "y26", "name": "2026", "mimeType": FOLDER_MIME, "parent": ROOT},
        {"id": "send26", "name": "2026春節送禮名單", "mimeType": SHEET_MIME, "parent": "y26"},
        {"id": "rootfile", "name": "春節送禮名單", "mimeType": SHEET_MIME, "parent": ROOT},
        {"id": "y27", "name": "2027", "mimeType": FOLDER_MIME, "parent": ROOT},
        {"id": "tp", "name": "2027儲值金結算-台北.xls", "mimeType": "application/vnd.ms-excel", "parent": "y27"},
        {"id": "tp_old", "name": "2027儲值金結算-台北", "mimeType": SHEET_MIME, "parent": "y27"},
        {"id": "tp_order", "name": "2027儲值金-台北.xlsx", "mimeType": "application/vnd.ms-excel", "parent": "y27"},
        {"id": "vip", "name": "2026VIP券-台北.xls", "mimeType": "application/vnd.ms-excel", "parent": "y27"},
    ])
    sheets = GiftSheets({"rootfile": ["統計", "2026春節送禮名單", "2025春節送禮名單"],
                         "send26": ["2025", "工作表4"]})
    drive.sheets = sheets
    sheets.reads[("rootfile", "'2026春節送禮名單'!A1")] = [['=importrange("send26","2025!a1:o")']]
    return drive, sheets


def _no_download(city, prev_year, log):
    raise RuntimeError("測試不連後台")


def _roll(drive, sheets, logs=None, **kw):
    kw.setdefault("gift_downloader", _no_download)
    return Rollover(drive, sheets, "master", 2027, log=(logs.append if logs is not None else lambda *_: None), **kw)


def test_gift_sources_prefer_raw_export_and_skip_others():
    drive, sheets = _setup()
    roll = _roll(drive, sheets)
    sources = roll._gift_sources(roll.children("y27"))
    assert list(sources) == ["台北"] and sources["台北"]["id"] == "tp"


def test_gift_download_replaces_area_file_and_falls_back():
    drive, sheets = _setup()
    calls = []

    def downloader(city, prev_year, log):
        calls.append((city, prev_year))
        if city != "台北":
            raise RuntimeError("沒有帳號")
        return b"PK\x03\x04", "xlsx", 3

    logs = []
    roll = _roll(drive, sheets, logs, gift_downloader=downloader)
    roll._gift_download(drive.items["y27"])
    assert calls == [(a, 2026) for a in nyg.GIFT_AREAS if a != "高雄"]   # 高雄從新竹拆出，不另外下載
    assert drive.items["tp"].get("trashed") is True                     # 同名舊 xls 換掉
    new = next(i for i in drive.items.values() if i["name"] == "2027儲值金結算-台北.xlsx")
    assert new["parent"] == "y27"
    assert not drive.items["tp_old"].get("trashed")                       # 已轉好的試算表不動
    assert roll._gift_sources(roll.children("y27"))["台北"]["id"] == new["id"]
    assert any("台中 後台下載失敗" in m for m in logs)


def test_gift_stats_builds_summary_and_reports_bands():
    drive, sheets = _setup()
    logs = []
    roll = _roll(drive, sheets, logs)
    # 轉檔暫存：copy 出來的新檔第一個分頁內容
    orig_copy = drive.copy

    def copy(fileId, body, **kw):
        req = orig_copy(fileId, body, **kw)

        def run():
            item = req.execute()
            sheets.tabs[item["id"]] = {"sheet1": 0}
            sheets.reads[(item["id"], "'sheet1'")] = [
                SETTLE_HEADER, _settle_row("王小明", "0912345678", "台北市大安區仁愛路1號", "L1",
                                           "2026-01-02", "儲值金50000", "50000")]
            return item
        return _Req(run)
    drive.copy = copy

    pivot_range = f"'台北儲值金'!V2:W"
    orig_batch_get = sheets.batchGet

    def batch_get(spreadsheetId, ranges, **kw):
        sheets.reads[(spreadsheetId, pivot_range)] = [["王小明", 50000], ["李大華", 130000], ["", 0]]
        return orig_batch_get(spreadsheetId, ranges, **kw)
    sheets.batchGet = batch_get

    records = roll.run_gift_stats()
    summary = next(i for i in drive.items.values() if i["name"] == "2027名單統整")
    assert summary["parent"] == "y27" and summary["mimeType"] == SHEET_MIME
    assert any(r["new_name"] == "2027名單統整" and r["created"] for r in records)
    sid = summary["id"]
    assert set(sheets.tabs[sid]) == {"設定", "郵遞區號", "2027送禮名單", "台北儲值金", "區間統計"}   # 預設分頁刪除
    # 轉檔暫存檔讀完移到垃圾桶
    temp = next(i for i in drive.items.values() if i["name"].endswith("_轉檔暫存"))
    assert temp.get("trashed") is True
    data = sheets.writes[(sid, "'台北儲值金'!A1")]
    assert data[0][1] == "客戶姓名" and data[1][3] == "0912345678" and data[1][8] == "50000"
    pivot = next(r["updateCells"] for _, r in sheets.requests if "updateCells" in r)
    assert pivot["start"]["columnIndex"] == len(SETTLE_HEADER) + 1
    date_filter = pivot["rows"][0]["values"][0]["pivotTable"]["filterSpecs"][0]
    assert date_filter["filterCriteria"]["condition"]["values"][0]["userEnteredValue"] == "=YEAR('購買日期')=2026"
    assert sheets.writes[(sid, "'郵遞區號'!A1:B375")][1] == ["100", "台北市中正區"]
    formula = sheets.writes[(sid, "'2027送禮名單'!A2")][0][0]
    assert "'台北儲值金'!V2:V" in formula and "'台北儲值金'!W2:W>=t" in formula
    assert "FILTER('台北儲值金'!$E:$E,'台北儲值金'!$B:$B=x,'台北儲值金'!$E:$E<>\"\")" in formula   # 地址取第一個非空白
    table = sheets.writes[(sid, "'區間統計'!A1")]
    assert table[0][3:] == ["5萬以下", "5萬～8萬", "8萬～10萬", "10萬～12萬", "12萬以上"]
    assert table[1][0] == "台北" and table[-1][0] == "全區"
    log = "\n".join(logs)
    assert "台北：2026 年 2 位" in log and "5萬～8萬 1 位" in log and "12萬以上 1 位" in log
    # 第一階段不產生送禮名單檔、不寫門檻
    assert not any(i["name"] == "2027春節送禮名單" for i in drive.items.values())
    assert (sid, "'設定'!B1") not in sheets.writes


def test_gift_stats_splits_kaohsiung_from_hsinchu():
    drive, sheets = _setup()
    del drive.items["tp"], drive.items["tp_old"], drive.items["tp_order"]
    drive.items["hc"] = {"id": "hc", "name": "2027儲值金結算-新竹", "mimeType": SHEET_MIME, "parent": "y27"}
    sheets.tabs["hc"] = {"sheet1": 0}
    sheets.reads[("hc", "'sheet1'")] = [
        SETTLE_HEADER,
        _settle_row("新竹甲", "0911", "新竹市東區1號", "", "2026-01-02", "儲值金50000", "50000"),
        _settle_row("高雄乙", "0922", "高雄市三民區1號", "", "2026-01-02", "儲值金50000", "50000"),
        _settle_row("不明丙", "0933", "", "", "2026-01-02", "儲值金50000", "50000"),   # 手機查到高雄
        _settle_row("不明丁", "0944", "", "", "2026-01-02", "儲值金50000", "50000"),   # 查不到 → 新竹
    ]
    lookups = []
    roll = _roll(drive, sheets, gift_phone_lookup=lambda p: lookups.append(p) or {"0933": "高雄"}.get(p, ""))
    roll.run_gift_stats()
    sid = next(i for i in drive.items.values() if i["name"] == "2027名單統整")["id"]
    hsinchu = [r[1] for r in sheets.writes[(sid, "'新竹儲值金'!A1")][1:]]
    kaohsiung = [r[1] for r in sheets.writes[(sid, "'高雄儲值金'!A1")][1:]]
    assert hsinchu == ["新竹甲", "不明丁"] and kaohsiung == ["高雄乙", "不明丙"]
    assert sorted(lookups) == ["0933", "0944"]                 # 分不出來的才查，各查一次


def _stage2(rows=None):
    drive, sheets = _setup()
    drive.items["sum27"] = {"id": "sum27", "name": "2027名單統整", "mimeType": SHEET_MIME, "parent": "y27"}
    gift_rows = rows if rows is not None else [
        ["台北", "王小明", "106", "台北市大安區", "台北市大安區仁愛路1號", "0912345678", "L1"]]
    orig_get = sheets.get

    def get(spreadsheetId, range=None, **kw):
        if range == "'2027送禮名單'!A2:G":
            return _Req(lambda: {"values": gift_rows})
        if range == "'2025'!A1":
            return _Req(lambda: {"values": [["2026春節送禮名單"]]})
        return orig_get(spreadsheetId, range, **kw)
    sheets.get = get
    return drive, sheets


def test_gift_list_writes_threshold_and_fills_send_file_and_root_tab():
    drive, sheets = _stage2()
    records = _roll(drive, sheets, gift_threshold=100000.0).run_gift_list()
    assert sheets.writes[("sum27", "'設定'!B1")] == [[100000]]
    send = next(i for i in drive.items.values() if i["name"] == "2027春節送禮名單")
    assert send["parent"] == "y27"
    sid = send["id"]
    assert (sid, "'2025'!J2:L") in sheets.cleared and (sid, "'2025'!A18:T") in sheets.cleared
    assert sheets.writes[(sid, "'2025'!A1")] == [["2027春節送禮名單"]]
    assert sheets.writes[(sid, "'2025'!B21:B21")] == [["王小明"]]
    assert sheets.writes[(sid, "'2025'!G21:G21")] == [["0912345678"]]   # 電話→G、LINE→H
    assert sheets.writes[(sid, "'2025'!H21:H21")] == [["L1"]]
    checkbox = next(r["repeatCell"] for f, r in sheets.requests if f == sid and "repeatCell" in r)
    assert checkbox["range"]["startRowIndex"] == 17 and checkbox["range"]["startColumnIndex"] == 5
    assert "2027春節送禮名單" in sheets.tabs["rootfile"]
    dup = next(r["duplicateSheet"] for f, r in sheets.requests if "duplicateSheet" in r)
    assert dup["insertSheetIndex"] == 1
    assert sheets.writes[("rootfile", "'2027春節送禮名單'!A1")] == [[f'=importrange("{sid}","2025!a1:o")']]
    assert any(r["kind"] == "工作表" and r["new_name"] == "2027春節送禮名單" for r in records)

    # 重跑：送禮名單檔已存在就不再清空重貼；勾只整理才重貼
    sheets.cleared.clear()
    _roll(drive, sheets, gift_threshold=80000).run_gift_list()
    assert not sheets.cleared and sheets.writes[("sum27", "'設定'!B1")] == [[80000]]
    _roll(drive, sheets, gift_threshold=80000, tidy=True).run_gift_list()
    assert (sid, "'2025'!J2:L") in sheets.cleared


def test_gift_list_requires_threshold_and_stage1():
    drive, sheets = _stage2()
    with pytest.raises(ValueError):
        _roll(drive, sheets).run_gift_list()
    del drive.items["sum27"]
    with pytest.raises(RuntimeError, match="春節禮品名單統計"):
        _roll(drive, sheets, gift_threshold=50000).run_gift_list()


def test_gift_list_no_match_creates_nothing():
    drive, sheets = _stage2(rows=[["沒有符合門檻的客戶"]])
    assert _roll(drive, sheets, gift_threshold=999999).run_gift_list() == []
    assert not any(i["name"] == "2027春節送禮名單" for i in drive.items.values())


def test_gift_no_sources_reports_missing():
    drive = GiftDrive([{"id": "y27", "name": "2027", "mimeType": FOLDER_MIME, "parent": ROOT}])
    records = _roll(drive, GiftSheets({})).run_gift_stats()
    assert records[-1]["new_name"] == "找不到來源"
    assert not any(i["name"] == "2027名單統整" for i in drive.items.values())


def test_gift_dry_run_creates_nothing():
    drive = GiftDrive([])
    records = _roll(drive, GiftSheets({}), dry_run=True).run_gift_stats()
    assert not drive.items and records[0]["new_id"] == "(dry-run:2027)"


def test_gift_helpers():
    assert gift_headers(["　　　　", "訂單編號", ""]) == ["欄A", "訂單編號", "欄C"]
    p = gift_pivot(5, 10, 20, 2026, "購買日期")
    assert p["rows"][0]["sourceColumnOffset"] == 1 and p["values"][0]["sourceColumnOffset"] == 8
    date, plan, name = p["filterSpecs"]
    assert date["columnOffsetIndex"] == 6 and all(f["filterCriteria"]["visibleByDefault"] for f in p["filterSpecs"])
    assert date["filterCriteria"]["condition"]["values"][0]["userEnteredValue"] == "=YEAR('購買日期')=2026"
    assert plan["columnOffsetIndex"] == 7 and plan["filterCriteria"]["condition"]["type"] == "TEXT_STARTS_WITH"
    assert plan["filterCriteria"]["condition"]["values"][0]["userEnteredValue"] == "儲值金"
    assert name["columnOffsetIndex"] == 1 and name["filterCriteria"]["condition"]["values"][0]["userEnteredValue"] == "保留"
    f = gift_list_formula([("台北", "台北儲值金", "V", "W"), ("桃園", "桃園儲值金", "V", "W")], 374)
    assert f.startswith("=LET(t,'設定'!$B$1,") and f.count("FILTER(") == 9
    assert "'郵遞區號'!$B$2:$B$375" in f and 'MAP(n,LAMBDA(x,"桃園"))' in f


def test_gift_bands():
    assert gift_band_labels() == ["5萬以下", "5萬～8萬", "8萬～10萬", "10萬～12萬", "12萬以上"]
    assert gift_band_counts([1000, 50000, 79999, 80000, 100000, 119999, 120000, 500000]) == [1, 2, 1, 2, 2]
    table = gift_stats_table([("台北", "台北儲值金", "BM", "BN"), ("桃園", "桃園儲值金", "BM", "BN")])
    assert len(table) == 4 and table[3] == ["全區"] + [f"=SUM({c}2:{c}3)" for c in "BCDEFGH"]
    assert table[1][4] == ('=COUNTIFS(\'台北儲值金\'!BM2:BM,"<>",\'台北儲值金\'!BN2:BN,">=50000",'
                           '\'台北儲值金\'!BN2:BN,"<80000")')


def test_run_dispatches_gift_stages(monkeypatch):
    roll = _roll(GiftDrive([]), GiftSheets({}))
    called = []
    monkeypatch.setattr(roll, "_run_rows", lambda *a, **k: called.append("rows") or [])
    monkeypatch.setattr(roll, "run_gift_stats", lambda: called.append("stats") or [])
    monkeypatch.setattr(roll, "run_gift_list", lambda: called.append("list") or [])
    roll.run(names=["春節禮品名單統計"]); roll.run(names=["春節送禮名單產生"])
    assert called == ["stats", "list"]


def test_zipcode_names_unique_and_not_prefix_of_each_other():
    names = [n for _, n in TW_ZIPCODES]
    assert len(names) == len(set(names)) and all("臺" not in n for n in names)
    assert not [(a, b) for a in names for b in names if a != b and b.startswith(a)]


def test_download_gift_source_uses_paid_dates_and_splits_hsinchu(monkeypatch):
    from tools.annual_rollover import gift_download
    from tools.scheduled_monthly import half_month_orders as hm

    class Res:
        content, headers, url = b"PK\x03\x04", {"Content-Type": "application/vnd.ms-excel"}, "x"

        def raise_for_status(self):
            pass

    got = {}
    monkeypatch.setattr(hm, "load_accounts", lambda: {c: {"email": "e", "password": "p"} for c in nyg.GIFT_AREAS})
    monkeypatch.setattr(hm, "login", lambda *a: None)
    monkeypatch.setattr(gift_download.requests.Session, "get",
                        lambda self, url, params=None, **k: got.update(params) or Res())
    monkeypatch.setattr(hm, "read_excel", lambda content: [1, 2])
    content, ext, count = gift_download.download_gift_source("台北", 2026, log=lambda *_: None)
    assert (ext, count) == ("xlsx", 2)
    assert got["buy"] == "5" and got["paid_at_s"] == "2026-01-01" and got["paid_at_e"] == "2026-12-31"
    assert got["clean_date_s"] == "" and got["purchase_status"] == "1"

    orders = [{"order_no": "A", "name": "王"}, {"order_no": "B", "name": "李"}]
    monkeypatch.setattr(hm, "purchase_records", lambda session, **f: orders)
    monkeypatch.setattr(hm, "stored_value_customer_city", lambda s, o: "新竹" if o["name"] == "王" else "高雄")
    monkeypatch.setattr(hm, "save_preserved_excel", lambda content, path: open(path, "wb").write(content))
    kept = {}

    def filt(src, out, selected):
        kept["orders"] = selected
        open(out, "wb").write(b"PKfiltered")
    monkeypatch.setattr(hm, "filter_workbook_rows", filt)
    content, _, _ = gift_download.download_gift_source("新竹", 2026, log=lambda *_: None)
    assert kept["orders"] == {"A"} and content == b"PKfiltered"
