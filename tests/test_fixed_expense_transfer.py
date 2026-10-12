import unittest
from unittest.mock import patch

from tools.finance_management import fixed_expense_transfer as transfer


def _values():
    values = [[""] * 8 for _ in range(74)]
    values[0][2] = "2026.08"
    values[1][2:8] = ["台北", "桃園", "新竹", "台中", "高雄", "總計"]
    for row, label, amount in [
        (41, "google行銷費", 11179),
        (42, "google服務費", 1118),
        (43, "facebook行銷費", 0),
        (46, "2%", 4401),
        (47, "內勤薪資", 10000),
        (48, "內勤薪資", 10000),
        (71, "稅捐", 586),
    ]:
        values[row - 1][1] = label
        values[row - 1][5] = amount
    values[71][5] = 36112
    values[73][5] = "已匯"
    return values


class FixedExpenseTransferTest(unittest.TestCase):
    def test_builds_taipei_area_and_request_rows(self):
        plan = transfer._build_plan(_values(), "202608", "台中")

        self.assertEqual(plan["total"], 36112)
        self.assertEqual(plan["items"], [
            {"subject": "google行銷費", "amount": 11179},
            {"subject": "google服務費", "amount": 1118},
            {"subject": "2%", "amount": 4401},
            {"subject": "內勤薪資", "amount": 10000},
            {"subject": "內勤薪資", "amount": 10000},
            {"subject": "稅捐", "amount": 586},
        ])
        self.assertEqual(plan["memo"], "2026/08google行銷費＋google服務費＋內勤薪資＋內勤薪資＋稅捐")
        self.assertEqual([(row[4], row[5], row[8]) for row in plan["taipei_rows"]], [
            (-11179, "", "google行銷費"),
            (-1118, "", "google服務費"),
            ("", 4401, "分店收入2%"),
            (-10000, "", "內勤薪資"),
            (-10000, "", "內勤薪資"),
            (586, "", "稅捐"),
        ])
        self.assertEqual([(row[4], row[5], row[8]) for row in plan["area_rows"]], [
            (11179, "", "google行銷費"),
            (1118, "", "google服務費"),
            (4401, "", "分店支出2%"),
            (10000, "", "內勤薪資"),
            (10000, "", "內勤薪資"),
            (-586, "", "稅捐"),
        ])
        self.assertTrue(all(row[11].endswith("台中") for row in plan["taipei_rows"]))

    def test_requires_positive_row_72_and_nonblank_row_74(self):
        values = _values()
        values[73][5] = ""
        self.assertIsNone(transfer._build_plan(values, "202608", "台中"))
        preview = transfer._build_plan(values, "202608", "台中", require_ready=False)
        self.assertEqual(preview["total"], 36112)
        self.assertFalse(preview["eligible"])
        values[73][5] = "已匯"
        values[71][5] = 0
        self.assertIsNone(transfer._build_plan(values, "202608", "台中"))
        self.assertIsNone(
            transfer._build_plan(values, "202608", "台中", require_ready=False)
        )

    def test_mismatch_stops_before_writing(self):
        values = _values()
        values[71][5] = 99999
        with self.assertRaisesRegex(RuntimeError, "第72列"):
            transfer._build_plan(values, "202608", "台中")

    def test_complete_existing_block_is_idempotent(self):
        rows = transfer._build_plan(_values(), "202608", "台中")["area_rows"]
        self.assertEqual(transfer._missing_statement_rows(rows, rows, "台中"), [])

    def test_appliance_routes_to_taoyuan_report(self):
        self.assertEqual(transfer.REPORT_AREA_NAMES["家電"], "桃園")

    def test_invalid_registry_gid_falls_back_to_2026_total_sheet(self):
        with patch.object(
            transfer,
            "resolve_marketing_expense_location",
            side_effect=RuntimeError("找不到 GID=無此工作表"),
        ):
            self.assertEqual(
                transfer._marketing_source_location(),
                (
                    "18KlxjxtbyjKGrmczSH3Eg3J8rem1JskhMjDE4M560m4",
                    "2026總表",
                ),
            )


if __name__ == "__main__":
    unittest.main()
