import ast
import re
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


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
        return SimpleNamespace(worksheets=lambda: self._worksheets)


class StoredValueSheetTest(unittest.TestCase):
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

    def test_does_not_replace_unrelated_sheet(self):
        client = FakeClient([FakeWorksheet("台北其他資料")])

        with self.assertRaisesRegex(WorksheetNotFound, "台北儲值金結算_YYYYMMDD"):
            load_writer()(client, "台北", [["客戶姓名"]])


if __name__ == "__main__":
    unittest.main()
