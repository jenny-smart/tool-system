"""2026-11-06 Jenny：排程盤點結果存檔，Tools App 跳視窗後直接停在盤點結果。"""
from tools.scheduling import scan_results as sr


class FakeSheets:
    def __init__(self):
        self.cells = {}  # row -> list
        self.titles = []

    # service chain
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, fields=None):
        self._pending = ("get", range, fields)
        return self

    def batchUpdate(self, spreadsheetId, body):
        self.titles.append(sr.SHEET_NAME)
        self._pending = ("noop",)
        return self

    def update(self, spreadsheetId, range, valueInputOption, body):
        row = int("".join(c for c in range.split("!")[1].split(":")[0] if c.isdigit()))
        col = "".join(c for c in range.split("!")[1].split(":")[0] if c.isalpha())
        base = self.cells.setdefault(row, [])
        start = ord(col) - 65
        values = body["values"][0]
        while len(base) < start + len(values):
            base.append("")
        base[start:start + len(values)] = values
        self._pending = ("noop",)
        return self

    def clear(self, spreadsheetId, range):
        row = int("".join(c for c in range.split("!")[1].split(":")[0] if c.isdigit()))
        self.cells.pop(row, None)
        self._pending = ("noop",)
        return self

    def execute(self):
        kind = self._pending[0]
        if kind == "get" and self._pending[2]:
            return {"sheets": [{"properties": {"title": t}} for t in self.titles]}
        if kind == "get":
            rows = [self.cells.get(r, []) for r in range(1, max(self.cells or {1: 0}) + 1)]
            return {"values": rows}
        return {}


def test_save_pending_load_mark():
    svc = FakeSheets()
    items = [{"region": "高雄", "row": 48, "actions": ["sync"], "raw": ["x" * 10]}]
    sr.save("change_order_scan", items, "可執行 1 筆", service=svc, spreadsheet_id="id")
    from datetime import datetime
    today = datetime.now(sr.TW).strftime("%Y/%m/%d")
    entries = sr.pending(today=today, service=svc, spreadsheet_id="id")
    assert [e["key"] for e in entries] == ["change_order_scan"] and entries[0]["summary"] == "可執行 1 筆"
    assert sr.load_items("change_order_scan", service=svc, spreadsheet_id="id") == items
    sr.mark("change_order_scan", service=svc, spreadsheet_id="id")
    assert sr.pending(today=today, service=svc, spreadsheet_id="id") == []
    assert sr.latest_today("change_order_scan", today=today, service=svc, spreadsheet_id="id")["status"] == "已開啟"
    # 再存一次覆蓋同一列
    sr.save("change_order_scan", items * 2, "可執行 2 筆", service=svc, spreadsheet_id="id")
    assert len(sr.load_items("change_order_scan", service=svc, spreadsheet_id="id")) == 2


def test_large_items_are_chunked():
    big = [{"raw": "y" * 60000}]
    chunks = sr.encode(big)
    assert len(chunks) == 2 and sr.decode(chunks) == big
