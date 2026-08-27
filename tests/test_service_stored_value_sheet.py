import ast
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class WorksheetNotFound(Exception):
    pass


def load_writer():
    path = Path(__file__).parents[1] / "tools/service_management/stored_value.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_write_stored_value_sheet"
    )
    namespace = {
        "Any": object,
        "gspread": SimpleNamespace(
            Client=object,
            Worksheet=object,
            WorksheetNotFound=WorksheetNotFound,
        ),
        "re": re,
        "STORED_VALUE_SPREADSHEET_IDS": {
            "台北": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
            "台中": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
        },
        "now_tp": lambda: datetime(2026, 8, 27),
        "fmt": lambda value, pattern: value.strftime(pattern),
        "log": SimpleNamespace(info=lambda *args: None),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_write_stored_value_sheet"]


def load_credentials():
    path = Path(__file__).parents[1] / "tools/service_management/stored_value.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_area_credentials"
    )
    namespace = {"get_secret_prefix": lambda area: "TAIPEI"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["get_area_credentials"]


class FakeWorksheet:
    def __init__(self, title):
        self.title = title
        self.row_count = 500
        self.col_count = 10
        self.cleared = False

    def update_title(self, title):
        self.title = title

    def clear(self):
        self.cleared = True

    def update(self, **kwargs):
        self.updated = kwargs

    def freeze(self, **kwargs):
        pass


class FakeClient:
    def __init__(self, worksheets):
        self._worksheets = worksheets
        self.opened_key = None

    def open_by_key(self, key):
        self.opened_key = key
        return self

    def worksheets(self):
        return self._worksheets

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet(title)
        worksheet.row_count = rows
        worksheet.col_count = cols
        self._worksheets.append(worksheet)
        return worksheet


class StoredValueSheetTest(unittest.TestCase):
    def test_credentials_reuse_monthly_settlement_accounts(self):
        monthly = SimpleNamespace(
            load_accounts=lambda: {
                "台北": {"email": "taipei@example.com", "password": "secret"}
            }
        )
        with patch.dict(
            sys.modules,
            {"tools.scheduled_monthly.stored_value_settlement": monthly},
        ):
            self.assertEqual(
                load_credentials()("台北"),
                ("taipei@example.com", "secret"),
            )

    def test_uses_monthly_sheet_and_only_matching_area(self):
        taipei = FakeWorksheet("台北儲值金結算_20260826")
        taichung = FakeWorksheet("台中儲值金結算_20260826")
        client = FakeClient([taipei, taichung])

        result = load_writer()(client, "台北", [["客戶姓名"], ["王小明"]])

        self.assertEqual(client.opened_key, "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE")
        self.assertIs(result, taipei)
        self.assertEqual(taipei.title, "台北儲值金結算_20260827")
        self.assertTrue(taipei.cleared)
        self.assertFalse(taichung.cleared)

    def test_creates_missing_area_sheet_without_replacing_unrelated_sheet(self):
        unrelated = FakeWorksheet("台北其他資料")
        client = FakeClient([unrelated])

        result = load_writer()(client, "台北", [["客戶姓名"]])

        self.assertEqual(result.title, "台北儲值金結算_20260827")
        self.assertFalse(unrelated.cleared)


if __name__ == "__main__":
    unittest.main()
