from datetime import datetime
import sys
import types
import unittest
from unittest.mock import patch


registry_stub = types.ModuleType("tools.bank_statement.internal_payment_registry")
registry_stub.DEPOSIT_REPORT_TYPE = "工具包押金財報"
registry_stub.resolve_report_location = lambda report_type, area: ("", "")

config_stub = types.ModuleType("tools.common.config_loader")
config_stub.get_master_spreadsheet_id = lambda: ""
config_stub.get_sheets_service = lambda: None

sys.modules.setdefault(
    "tools.bank_statement.internal_payment_registry",
    registry_stub,
)
sys.modules.setdefault("tools.common.config_loader", config_stub)

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


class DepositReportAmountTests(unittest.TestCase):
    def _sheet_patches(self, values, service):
        return (
            patch.object(
                deposit_report,
                "resolve_report_location",
                return_value=("spreadsheet-id", "押金"),
            ),
            patch.object(deposit_report, "_read_values", return_value=values),
            patch.object(deposit_report, "get_sheets_service", return_value=service),
        )

    def test_deposit_amount_by_area(self):
        self.assertEqual(deposit_report._deposit_amount("台北"), 2000)
        self.assertEqual(deposit_report._deposit_amount("台中"), 1500)

    def test_unknown_deposit_area_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支援"):
            deposit_report._deposit_amount("桃園")

    def test_taichung_monthly_aggregate_uses_1500(self):
        service = _Service()
        results = {
            "上課": (["王小明"], 1),
            "退還": ([], 0),
            "已退": ([], 0),
            "不退": ([], 0),
        }
        sheet_patches = self._sheet_patches([["header"]], service)
        with sheet_patches[0], sheet_patches[1], sheet_patches[2], patch.object(
            deposit_report,
            "_aggregate",
            side_effect=lambda values, type_, year_month: results[type_],
        ):
            written = deposit_report._aggregate_month_to_uy("台中", "202608")

        self.assertEqual(written, 1)
        first_row = service.values_api.batch_body["data"][0]["values"][0]
        self.assertEqual(
            first_row,
            ["上課：王小明＋共1人", "202608上課", 1500, 0],
        )

    def test_taichung_discrepancy_expects_1500(self):
        year = datetime.now(deposit_report.TW_TZ).year
        row = ["王小明", 1500, f"{year}-01-01"] + [""] * 6 + [True]
        service = _Service()
        sheet_patches = self._sheet_patches([["header"], row], service)
        with sheet_patches[0], sheet_patches[1], sheet_patches[2]:
            result = deposit_report._flag_discrepancies("台中")

        self.assertEqual(result, {"flagged": 0, "checked": 1})

    def test_taipei_discrepancy_still_expects_2000(self):
        year = datetime.now(deposit_report.TW_TZ).year
        row = ["王小明", 1500, f"{year}-01-01"] + [""] * 6 + [True]
        service = _Service()
        sheet_patches = self._sheet_patches([["header"], row], service)
        with sheet_patches[0], sheet_patches[1], sheet_patches[2]:
            result = deposit_report._flag_discrepancies("台北")

        self.assertEqual(result, {"flagged": 1, "checked": 1})


if __name__ == "__main__":
    unittest.main()
