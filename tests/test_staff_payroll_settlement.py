from tools.staff_payroll import settlement


class FakeWs:
    def __init__(self, values=None):
        self.values = values or []
        self.acells = {}

    def get_all_values(self):
        return self.values

    def update_acell(self, cell, value):
        self.acells[cell] = value


class FakeSpreadsheet:
    def __init__(self, sheets):
        self.sheets = sheets

    def worksheet(self, name):
        return self.sheets[name]


class FakeSheets:
    def __init__(self):
        self.cleared = []
        self.written = []

    def clear_from_row(self, ws, start_row, start_col, end_col):
        self.cleared.append((start_row, start_col, end_col))

    def write_values(self, ws, start_row, start_col, values):
        self.written.append((start_row, start_col, values))


SUMMARY = [
    ["月份", "月份姓名", "員工編號", "姓名"],
    ["2026.09", "2026.09鄭淵湶", "L0040", "鄭淵湶"],
    ["2026.10", "2026.10賴彥妃", "L0001", "賴彥妃"],
    ["2026.1", "2026.10張齊恩", "L0035", "張齊恩"],
    ["2026.01", "2026.01薛湘霖", "L0041", "薛湘霖"],
]


def _run(monkeypatch, summary):
    payroll = FakeWs()
    book = FakeSpreadsheet({"薪資單": payroll, "2026薪資總表": FakeWs(summary)})
    monkeypatch.setattr(settlement, "_open_area_summary", lambda *a: book)
    sheets = FakeSheets()
    return settlement.run_settlement(None, sheets, "台北", "202610"), payroll, sheets


def test_names_follow_month_and_ac1_is_text(monkeypatch):
    result, payroll, sheets = _run(monkeypatch, SUMMARY)
    assert result["names"] == ["賴彥妃", "張齊恩"]
    assert payroll.acells["AC1"] == "'2026.10"
    assert sheets.cleared == [(3, 2, 4)]
    assert sheets.written == [(3, 2, [["賴彥妃"], ["張齊恩"]]), (3, 4, [[True], [True]])]


def test_missing_month_does_not_touch_sheet(monkeypatch):
    try:
        _run(monkeypatch, SUMMARY[:2])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
