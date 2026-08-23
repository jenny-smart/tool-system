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
    get_master_spreadsheet_id=lambda: "",
    get_sheets_service=lambda: None,
)
_stub_module(
    "tools.finance_management.execution_log",
    log_execution=lambda *args, **kwargs: None,
)
_stub_module(
    "tools.finance_management.statement_registry",
    resolve_statement_location=lambda *args, **kwargs: ("", ""),
)

from tools.finance_management import statement_rules


def _academy_rule(name, category, suffix, action, set_e=""):
    return statement_rules.Rule([
        True, name, "J", "包含", "學院薪資", "", "", "", category, 0, suffix,
        False, action, "", "", "J", set_e,
    ])


class _Response:
    def execute(self):
        return {}


class _FakeValues:
    def __init__(self):
        self.batch_updates = []
        self.updates = []

    def batchUpdate(self, **kwargs):
        self.batch_updates.append(kwargs)
        return _Response()

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


class AcademyStatementRulesTest(unittest.TestCase):
    def test_l_value_can_use_year_month_from_j(self):
        rule = _academy_rule("學院薪資", "內勤薪資", "內勤薪資-學院", "更新原列")
        for marker in (
            "202605學院薪資",
            "2026.05學院薪資",
            "2026-05學院薪資",
            "2026/05學院薪資",
        ):
            with self.subTest(marker=marker):
                row = [""] * 12
                row[9] = marker
                row[10] = "2026/08/19"
                self.assertEqual(rule.l_value(row), "2026.05-內勤薪資-學院")

    def test_existing_k_month_source_stays_compatible(self):
        rule = statement_rules.Rule([
            True, "既有規則", "D", "等於", "勞保費", "", "", "", "內勤勞保費",
            -2, "內勤勞保費", False, "更新原列", "", "",
        ])
        row = [""] * 12
        row[10] = "2026/08/19"

        self.assertEqual(rule.month_source, "K")
        self.assertEqual(rule.l_value(row), "2026.06-內勤勞保費")

    def test_fixed_e_amount_accepts_formatted_number(self):
        rule = _academy_rule(
            "學院退休金", "內勤退休金", "內勤退休金-學院", "插入新列", "-2,406"
        )
        self.assertEqual(rule.set_e, -2406)

    def test_academy_salary_marker_matches_all_four_rules(self):
        row = [""] * 12
        row[9] = "202605學院薪資"
        rules = [
            _academy_rule("學院薪資", "內勤薪資", "內勤薪資-學院", "更新原列"),
            _academy_rule("學院退休金", "內勤退休金", "內勤退休金-學院", "插入新列", -2406),
            _academy_rule("學院勞保費", "內勤勞保費", "內勤勞保費-學院", "插入新列", -4611),
            _academy_rule("學院健保費", "內勤健保費", "內勤健保費-學院", "插入新列", -2518),
        ]

        self.assertTrue(all(rule.matches(row) for rule in rules))

    def test_academy_marker_updates_salary_and_inserts_three_fixed_expenses(self):
        rules = [
            _academy_rule("學院薪資", "內勤薪資", "內勤薪資-學院", "更新原列"),
            _academy_rule("學院退休金", "內勤退休金", "內勤退休金-學院", "插入新列", -2406),
            _academy_rule("學院勞保費", "內勤勞保費", "內勤勞保費-學院", "插入新列", -4611),
            _academy_rule("學院健保費", "內勤健保費", "內勤健保費-學院", "插入新列", -2518),
        ]
        source_row = [""] * 18
        source_row[4] = -49411
        source_row[8] = "內勤薪資"
        source_row[9] = "202605學院薪資"
        source_row[10] = "2026/08/19"
        fake_service = _FakeService()

        with (
            patch.object(statement_rules, "load_rules", return_value=rules),
            patch.object(
                statement_rules,
                "resolve_statement_location",
                return_value=("spreadsheet", "承攬費"),
            ),
            patch.object(statement_rules, "_read_values", return_value=[["標題"], source_row]),
            patch.object(statement_rules, "get_sheets_service", return_value=fake_service),
            patch.object(statement_rules, "_sheet_id_for_title", return_value=123),
        ):
            result = statement_rules._apply_rules_impl("台北")

        self.assertEqual(result["inserted_rows"], 3)
        inserted = fake_service.spreadsheets_api.values_api.updates[0]["body"]["values"]
        self.assertEqual(
            [(row[8], row[4], row[11]) for row in inserted],
            [
                ("內勤退休金", -2406, "2026.05-內勤退休金-學院"),
                ("內勤勞保費", -4611, "2026.05-內勤勞保費-學院"),
                ("內勤健保費", -2518, "2026.05-內勤健保費-學院"),
            ],
        )

    def test_processed_academy_row_is_not_inserted_again(self):
        source_row = [""] * 18
        source_row[9] = "202605學院薪資"
        source_row[16] = "2026/08/24 01:30:00"
        fake_service = _FakeService()
        rules = [
            _academy_rule("學院退休金", "內勤退休金", "內勤退休金-學院", "插入新列", -2406)
        ]

        with (
            patch.object(statement_rules, "load_rules", return_value=rules),
            patch.object(
                statement_rules,
                "resolve_statement_location",
                return_value=("spreadsheet", "承攬費"),
            ),
            patch.object(statement_rules, "_read_values", return_value=[["標題"], source_row]),
            patch.object(statement_rules, "get_sheets_service", return_value=fake_service),
        ):
            result = statement_rules._apply_rules_impl("台北")

        self.assertEqual(result, {"updated_rows": 0, "inserted_rows": 0})
        self.assertEqual(fake_service.spreadsheets_api.values_api.updates, [])


if __name__ == "__main__":
    unittest.main()
