import sys
import types
import unittest
from unittest.mock import patch


def _stub_module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules.setdefault(name, module)
    return module


_stub_module("tools.common").__path__ = []
_stub_module(
    "tools.common.config_loader",
    get_master_spreadsheet_id=lambda: "master",
    get_sheets_service=lambda: None,
)
_stub_module("tools.finance_management.execution_log", log_execution=lambda *args, **kwargs: None)
_stub_module(
    "tools.finance_management.statement_registry",
    resolve_marketing_expense_location=lambda area: ("marketing", "行銷總表"),
    resolve_statement_location=lambda *args, **kwargs: ("statement", "財務"),
    sheet_title_for_gid=lambda *args, **kwargs: "固定分頁",
)

from tools.finance_management import cash_gap


class _Response:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def execute(self):
        return self.payload


class _FakeValues:
    def __init__(self):
        self.gets = []

    def get(self, **kwargs):
        self.gets.append(kwargs)
        return _Response({"values": [[1234]]})


class _FakeSpreadsheets:
    def __init__(self):
        self.values_api = _FakeValues()

    def values(self):
        return self.values_api


class _FakeService:
    def __init__(self):
        self.spreadsheets_api = _FakeSpreadsheets()

    def spreadsheets(self):
        return self.spreadsheets_api


class CashGapMarketingSettingsTest(unittest.TestCase):
    def test_marketing_source_uses_area_settings_id_and_gid_title(self):
        service = _FakeService()

        with (
            patch.object(
                cash_gap,
                "resolve_marketing_expense_location",
                return_value=("dynamic-marketing-id", "dynamic-marketing-title"),
            ) as resolve_location,
            patch.object(cash_gap, "get_sheets_service", return_value=service),
        ):
            amount = cash_gap.marketing_expense_row("桃園", 7, cash_gap.MARKETING_ROW_AD)

        self.assertEqual(amount, 1234)
        resolve_location.assert_called_once_with("桃園")
        request = service.spreadsheets_api.values_api.gets[0]
        self.assertEqual(request["spreadsheetId"], "dynamic-marketing-id")
        self.assertEqual(request["range"], "'dynamic-marketing-title'!AT5")


if __name__ == "__main__":
    unittest.main()
