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
_stub_module(
    "tools.finance_management.statement_registry",
    resolve_statement_location=lambda *args, **kwargs: ("statement", "承攬費"),
    resolve_marketing_expense_location=lambda *args, **kwargs: ("marketing", "2026總表"),
)

from tools.finance_management import intercompany_expense as expense


class _Response:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def execute(self):
        return self.payload


class _FakeValues:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return _Response()


class _FakeSpreadsheets:
    def __init__(self):
        self.values_api = _FakeValues()
        self.batch_updates = []

    def values(self):
        return self.values_api

    def batchUpdate(self, **kwargs):
        self.batch_updates.append(kwargs)
        return _Response()


class _FakeService:
    def __init__(self):
        self.spreadsheets_api = _FakeSpreadsheets()

    def spreadsheets(self):
        return self.spreadsheets_api


def _rules():
    return [
        expense.ExpenseRule("google行銷費", "Google行銷", "費用減項：E欄負數", "費用：E欄正數", "Google行銷費-支付區"),
        expense.ExpenseRule("2%", "分店收入-2", "收入：F欄正數", "費用：E欄正數", "2%-支付區"),
        expense.ExpenseRule("稅捐", "稅捐", "費用：E欄正數", "費用減項：E欄負數", "稅捐-支付區"),
    ]


def _marketing_values(control=125):
    rows = [
        ["", "", "2026.05", ""],
        ["", "", "桃園", "新竹"],
        ["", "google行銷費", "100", "80"],
        ["", "2%", "30", "20"],
        ["", "稅捐", "5", "4"],
        ["內部資金收入/支出", "", "135", "96"],
        ["內部資金收入/支出", "", str(control), "88"],
    ]
    return rows


class IntercompanyExpenseTest(unittest.TestCase):
    def test_finds_region_under_merged_month_header(self):
        values = [
            ["", "", "2026.04", "", "", "2026.05", ""],
            ["", "", "台北", "桃園", "新竹", "台北", "桃園"],
        ]
        self.assertEqual(expense._find_source_column(values, "202605", "桃園"), 7)

    def test_taipei_and_payer_use_different_signs(self):
        rows = expense._source_rows(_marketing_values(), 3)
        taipei = expense._build_details(_rules(), rows, "202605", "桃園", "台北", 125)
        payer = expense._build_details(_rules(), rows, "202605", "桃園", "桃園", 125)

        self.assertEqual([(x["e"], x["f"]) for x in taipei], [(-100, 0), (0, 30), (5, 0)])
        self.assertEqual([(x["e"], x["f"]) for x in payer], [(100, 0), (30, 0), (-5, 0)])
        self.assertEqual(sum(x["f"] - x["e"] for x in taipei), 125)
        self.assertEqual(sum(x["f"] - x["e"] for x in payer), -125)

    def test_small_rounding_residual_is_put_on_first_detail(self):
        rows = expense._source_rows(_marketing_values(control=126), 3)
        details = expense._build_details(_rules(), rows, "202605", "桃園", "台北", 126)

        self.assertEqual(details[0]["e"], -101)
        self.assertEqual(sum(x["f"] - x["e"] for x in details), 126)

    def test_large_unreconciled_difference_stops(self):
        rows = expense._source_rows(_marketing_values(control=200), 3)
        with self.assertRaisesRegex(RuntimeError, "無法勾稽"):
            expense._build_details(_rules(), rows, "202605", "桃園", "台北", 200)

    def test_marker_updates_total_row_and_inserts_remaining_details(self):
        source_row = [""] * 18
        source_row[1] = "2026/07/31"
        source_row[4] = 125
        source_row[9] = "202605桃園代墊"
        fake_service = _FakeService()

        with (
            patch.object(expense, "resolve_statement_location", return_value=("statement", "承攬費")),
            patch.object(expense, "_read_statement_values", return_value=[["標題"], source_row]),
            patch.object(expense, "load_expense_rules", return_value=_rules()),
            patch.object(expense, "resolve_marketing_expense_location", return_value=("marketing", "2026總表")),
            patch.object(expense, "_read_marketing_values", return_value=_marketing_values()),
            patch.object(expense, "get_sheets_service", return_value=fake_service),
            patch.object(expense, "_sheet_id_for_title", return_value=123),
        ):
            result = expense.apply_intercompany_expense_rules("桃園")

        self.assertEqual(result, {"updated_rows": 1, "inserted_rows": 2})
        original = fake_service.spreadsheets_api.values_api.updates[0]["body"]["values"][0]
        inserted = fake_service.spreadsheets_api.values_api.updates[1]["body"]["values"]
        self.assertEqual((original[4], original[5], original[8]), (100, 0, "Google行銷"))
        self.assertEqual([(row[4], row[5], row[8]) for row in inserted], [
            (30, 0, "分店收入-2"),
            (-5, 0, "稅捐"),
        ])
        self.assertTrue(all(row[16] for row in [original, *inserted]))

    def test_bank_total_mismatch_does_not_write(self):
        source_row = [""] * 18
        source_row[1] = "2026/07/31"
        source_row[4] = 999
        source_row[9] = "202605桃園代墊"
        fake_service = _FakeService()

        with (
            patch.object(expense, "_read_statement_values", return_value=[["標題"], source_row]),
            patch.object(expense, "load_expense_rules", return_value=_rules()),
            patch.object(expense, "_read_marketing_values", return_value=_marketing_values()),
            patch.object(expense, "get_sheets_service", return_value=fake_service),
        ):
            with self.assertRaisesRegex(RuntimeError, "銀行總額"):
                expense.apply_intercompany_expense_rules("桃園")

        self.assertEqual(fake_service.spreadsheets_api.values_api.updates, [])

    def test_non_matching_payer_area_stops(self):
        source_row = [""] * 18
        source_row[1] = "2026/07/31"
        source_row[4] = 125
        source_row[9] = "202605桃園代墊"
        with patch.object(expense, "_read_statement_values", return_value=[["標題"], source_row]):
            with self.assertRaisesRegex(RuntimeError, "目前執行地區是 新竹"):
                expense.apply_intercompany_expense_rules("新竹")


if __name__ == "__main__":
    unittest.main()
