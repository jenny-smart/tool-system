from tools.annual_rollover import office_rollover as orl

VALUES = [
    ["", "評價日期", "姓名", "電話", "地址", "預約日期"],
    ["LC1", "", "曾韻華", "", "", "4/8"],                           # 沒年份 → 刪
    ["LC2", "", "黃文玲", "", "", "2026-12-16 08:30-12:30"],        # 2026 → 刪
    ["LC3", "", "陳彥如", "", "", "115/05/15(五)平日下午3"],          # 民國 115＝2026 → 刪
    [],
    ["", "", "10月之後預約"],                                        # 區塊標題 → 留
    ["", "", "蔡明哲", "0905388227", "", "2027-01-23"],              # 2027 → 留
    ["", "", "李珊緹", "", "", "2027/4/17"],                         # 2027 → 留
    ["", "", "林韻珊", "", "", "待約12月"],                           # 沒年份 → 刪
    [],
    ["", "", "單次付款定期"],                                        # 定期區塊 → 整段留
    ["", "", "王小姐", "0972358158", "", "1人4小 下午/每2週一次"],
    ["", "", "定期VIP每月需通知"],
    ["", "", "陳若婕", "", "車馬費"],
]


def test_rows_to_delete_keeps_only_new_year_and_recurring_sections():
    assert orl.rows_to_delete(VALUES, 2027) == [2, 3, 4, 9]


def test_row_year_formats():
    assert orl.row_year(["", "", "", "", "", "2027-01-27"]) == 2027
    assert orl.row_year(["", "", "", "", "", "116/1/5 上午"]) == 2027
    assert orl.row_year(["", "", "", "", "", "5/1"]) is None


def test_runs_bottom_up():
    assert orl._runs([2, 3, 4, 9]) == [(8, 9), (1, 4)]


class _Req:
    def __init__(self, r):
        self.r = r

    def execute(self):
        return self.r


class FakeSheets:
    def __init__(self, files):
        self.files, self.calls, self._next = files, [], 500

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def sheets(self):
        return self

    def get(self, spreadsheetId, fields=None, range=None):
        if fields:
            return _Req({"sheets": [{"properties": p} for p in self.files[spreadsheetId]["props"]]})
        return _Req({"values": self.files[spreadsheetId]["values"]})

    def copyTo(self, spreadsheetId, sheetId, body):
        self._next += 1
        self.calls.append(("copyTo", spreadsheetId, sheetId, body["destinationSpreadsheetId"]))
        return _Req({"sheetId": self._next})

    def batchUpdate(self, spreadsheetId, body):
        self.calls.append(("batchUpdate", spreadsheetId, body["requests"]))
        return _Req({})


def _props(title, sid, rows=1000):
    return {"title": title, "sheetId": sid, "index": 0, "gridProperties": {"rowCount": rows, "columnCount": 26}}


def test_rollover_rating_pastes_into_existing_tab_and_drops_temp():
    fake = FakeSheets({
        "prev": {"props": [_props("評價預約", 11, 1000), _props("VIP名單", 12)], "values": VALUES},
        "new": {"props": [_props("評價預約", 21, 900)], "values": []},
    })
    out = orl.rollover_rating(fake, "prev", "new", 2027, log=lambda m: None)
    assert out == {"deleted": 4, "kept": 7}
    reqs = [c for c in fake.calls if c[0] == "batchUpdate"][-1][2]
    kinds = [list(r)[0] for r in reqs]
    assert kinds[:2] == ["deleteDimension", "deleteDimension"]
    assert "deleteSheet" in kinds and kinds[-1] == "deleteSheet"
    assert not any(list(r)[0] == "deleteSheet" and r["deleteSheet"]["sheetId"] == 21 for r in reqs)  # 原分頁不刪
    paste = next(r["copyPaste"] for r in reqs if "copyPaste" in r)
    assert paste["destination"]["sheetId"] == 21 and paste["source"]["endRowIndex"] == 996


def test_copy_missing_tabs_only_missing_and_existing_in_prev():
    fake = FakeSheets({
        "prev": {"props": [_props("VIP名單", 12), _props("捷運站表", 13)], "values": []},
        "new": {"props": [_props("捷運站表", 23)], "values": []},
    })
    added = orl.copy_missing_tabs(fake, "prev", "new", log=lambda m: None)
    assert added == ["VIP名單"]
    assert fake.calls[0][:3] == ("copyTo", "prev", 12)


def test_tool_system_holidays_fall_back_to_synced_calendar(monkeypatch):
    from datetime import date

    from tools.common import tw_holidays

    monkeypatch.setattr(tw_holidays, "_fetch", lambda year, timeout=10: None)
    monkeypatch.setitem(tw_holidays.__dict__, "_CACHE", {})
    holidays, _makeup, official = tw_holidays.calendar_for(2026)
    assert official and date(2026, 9, 28) in holidays   # 辦公日曆有教師節
