from datetime import datetime

import pytest

from tools.finance_management import deposit_report


class _Execute:
    def execute(self):
        return {}


class _ValuesApi:
    def __init__(self):
        self.batch_body = None
        self.update_body = None

    def batchUpdate(self, *, spreadsheetId, body):
        self.batch_body = body
        return _Execute()

    def update(self, *, spreadsheetId, range, valueInputOption, body):
        self.update_body = body
        return _Execute()


class _SpreadsheetsApi:
    def __init__(self, values_api):
        self._values_api = values_api

    def values(self):
        return self._values_api


class _Service:
    def __init__(self):
        self.values_api = _ValuesApi()

    def spreadsheets(self):
        return _SpreadsheetsApi(self.values_api)


def _patch_sheet_access(monkeypatch, values):
    service = _Service()
    monkeypatch.setattr(
        deposit_report,
        "resolve_report_location",
        lambda report_type, area: ("spreadsheet-id", "押金"),
    )
    monkeypatch.setattr(deposit_report, "_read_values", lambda spreadsheet_id, title: values)
    monkeypatch.setattr(deposit_report, "get_sheets_service", lambda: service)
    return service


@pytest.mark.parametrize(
    ("area", "expected"),
    [("台北", 2000), ("台中", 1500)],
)
def test_deposit_amount_by_area(area, expected):
    assert deposit_report._deposit_amount(area) == expected


def test_unknown_deposit_area_is_rejected():
    with pytest.raises(ValueError, match="不支援"):
        deposit_report._deposit_amount("桃園")


def test_taichung_monthly_aggregate_uses_1500(monkeypatch):
    service = _patch_sheet_access(monkeypatch, [["header"]])
    results = {
        "上課": (["王小明"], 1),
        "退還": ([], 0),
        "已退": ([], 0),
        "不退": ([], 0),
    }
    monkeypatch.setattr(
        deposit_report,
        "_aggregate",
        lambda values, type_, year_month: results[type_],
    )

    assert deposit_report._aggregate_month_to_uy("台中", "202608") == 1

    first_row = service.values_api.batch_body["data"][0]["values"][0]
    assert first_row == ["上課：王小明＋共1人", "202608上課", 1500, 0]


def test_taichung_discrepancy_expects_1500(monkeypatch):
    year = datetime.now(deposit_report.TW_TZ).year
    row = ["王小明", 1500, f"{year}-01-01"] + [""] * 6 + [True]
    _patch_sheet_access(monkeypatch, [["header"], row])

    assert deposit_report._flag_discrepancies("台中") == {
        "flagged": 0,
        "checked": 1,
    }


def test_taipei_discrepancy_still_expects_2000(monkeypatch):
    year = datetime.now(deposit_report.TW_TZ).year
    row = ["王小明", 1500, f"{year}-01-01"] + [""] * 6 + [True]
    _patch_sheet_access(monkeypatch, [["header"], row])

    assert deposit_report._flag_discrepancies("台北") == {
        "flagged": 1,
        "checked": 1,
    }
