import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError
from httplib2 import Response

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
            {"row_number": 41, "subject": "google行銷費", "amount": 11179},
            {"row_number": 42, "subject": "google代理商服務費", "amount": 1118},
            {"row_number": 46, "subject": "2%", "amount": 4401},
            {"row_number": 47, "subject": "內勤薪資", "amount": 10000},
            {"row_number": 48, "subject": "內勤薪資", "amount": 10000},
            {"row_number": 71, "subject": "稅捐", "amount": 586},
        ])
        self.assertEqual(plan["memo"], "2026/08google行銷費＋google代理商服務費＋內勤薪資＋內勤薪資＋稅捐")
        self.assertEqual([(row[4], row[5], row[8]) for row in plan["taipei_rows"]], [
            (-11179, "", "google行銷費"),
            (-1118, "", "google代理商服務費"),
            ("", 4401, "分店收入2%"),
            (-10000, "", "內勤薪資"),
            (-10000, "", "內勤薪資"),
            (586, "", "稅捐"),
        ])
        self.assertEqual([(row[4], row[5], row[8]) for row in plan["area_rows"]], [
            (11179, "", "google行銷費"),
            (1118, "", "google代理商服務費"),
            (4401, "", "分店支出2%"),
            (10000, "", "內勤薪資"),
            (10000, "", "內勤薪資"),
            (-586, "", "稅捐"),
        ])
        self.assertTrue(all(row[11].endswith("-台中") for row in plan["taipei_rows"]))
        self.assertEqual(plan["taipei_rows"][2][11], "2026.08-2%-台中")
        self.assertEqual(plan["taipei_rows"][-1][11], "2026.08-稅捐-台中")

    def test_legacy_wrong_labels_match_for_in_place_correction(self):
        plan = transfer._build_plan(_values(), "202608", "台中")
        correct = plan["taipei_rows"][2]
        legacy = list(correct)
        legacy[11] = "2026.08-google服務費台中"
        self.assertEqual(transfer._fingerprint(legacy), transfer._fingerprint(correct))
        self.assertNotEqual(transfer._raw_fingerprint(legacy), transfer._raw_fingerprint(correct))

    def test_requires_positive_row_72_but_not_row_74(self):
        values = _values()
        values[73][5] = ""
        preview = transfer._build_plan(values, "202608", "台中")
        self.assertEqual(preview["total"], 36112)
        self.assertFalse(preview["eligible"])
        values[73][5] = "已匯"
        values[71][5] = 0
        self.assertIsNone(transfer._build_plan(values, "202608", "台中"))
        self.assertIsNone(
            transfer._build_plan(values, "202608", "台中", require_ready=False)
        )

    def test_writes_bank_payment_date_to_source_row_74(self):
        service = unittest.mock.MagicMock()
        with patch.object(
            transfer,
            "_marketing_source_location",
            return_value=("sheet-id", "2026總表"),
        ), patch.object(transfer, "_read_values", return_value=_values()), patch.object(
            transfer, "get_sheets_service", return_value=service
        ):
            cell = transfer.write_transfer_date("202608", "台中", "2026/10/13")

        self.assertEqual(cell, "F74")
        service.spreadsheets().values().update.assert_called_once_with(
            spreadsheetId="sheet-id",
            range="'2026總表'!F74",
            valueInputOption="USER_ENTERED",
            body={"values": [["2026/10/13"]]},
        )

    def test_row_74_write_falls_back_to_user_oauth_on_403(self):
        service = unittest.mock.MagicMock()
        oauth_service = unittest.mock.MagicMock()
        forbidden = HttpError(Response({"status": "403"}), b"forbidden")
        service.spreadsheets().values().update().execute.side_effect = forbidden
        with patch.object(
            transfer,
            "_marketing_source_location",
            return_value=("sheet-id", "2026總表"),
        ), patch.object(transfer, "_read_values", return_value=_values()), patch.object(
            transfer, "get_sheets_service", return_value=service
        ), patch(
            "services.google_auth.get_sheets_service", return_value=oauth_service
        ):
            cell = transfer.write_transfer_date("202608", "台中", "2026/10/13")

        self.assertEqual(cell, "F74")
        oauth_service.spreadsheets().values().update.assert_called_once()

    def test_mismatch_stops_before_writing(self):
        values = _values()
        values[71][5] = 99999
        with self.assertRaisesRegex(RuntimeError, "第72列"):
            transfer._build_plan(values, "202608", "台中")

    def test_complete_existing_block_is_idempotent(self):
        rows = transfer._build_plan(_values(), "202608", "台中")["area_rows"]
        self.assertEqual(transfer._missing_statement_rows(rows, rows, "台中"), [])

    def test_partial_existing_rows_only_returns_missing_rows(self):
        rows = transfer._build_plan(_values(), "202608", "台中")["area_rows"]
        self.assertEqual(
            transfer._missing_statement_rows(rows[:2], rows, "台中"), rows[2:]
        )

    def test_matching_rows_keep_physical_positions_for_reordering(self):
        plan = transfer._build_target_plan(self._taoyuan_and_appliance_values(), "202608", "桃園")
        planned = plan["area_rows"]
        scrambled = list(reversed(planned))

        self.assertEqual(
            transfer._matching_statement_row_numbers(scrambled, planned),
            list(range(1, len(planned) + 1)),
        )

    def test_matching_rows_ignore_amount_headers(self):
        planned = transfer._build_plan(_values(), "202608", "台中")["area_rows"]
        header = ["", "", "", "", "支出金額", "收入金額", "", "", "科目"]
        self.assertEqual(
            transfer._matching_statement_row_numbers([header, planned[0]], planned),
            [2],
        )

    def test_taoyuan_combines_appliance_subjects_and_amounts(self):
        values = self._taoyuan_and_appliance_values()
        plan = transfer._build_target_plan(values, "202608", "桃園")

        self.assertEqual(plan["area"], "桃園")
        self.assertEqual(plan["source_areas"], ["桃園", "家電"])
        self.assertEqual(plan["total"], 1500)
        self.assertEqual(
            [(item["source_area"], item["amount"]) for item in plan["items"]],
            [("桃園", 500), ("家電", 1000)],
        )
        self.assertTrue(plan["taipei_rows"][0][11].endswith("桃園"))
        self.assertTrue(plan["taipei_rows"][1][11].endswith("家電"))

    @staticmethod
    def _taoyuan_and_appliance_values():
        values = _values()
        for row in values:
            row.append("")
        values[1][8] = "家電"
        values[40][3] = 500
        values[71][3] = 500
        values[40][8] = 1000
        values[71][8] = 1000
        return values

    def test_selected_subject_rows_recalculate_transfer_and_memo(self):
        plan = transfer._build_plan(_values(), "202608", "台中")
        selected = transfer.select_plan_rows(plan, {41, 47, 71})

        self.assertEqual(selected["total"], 11179 + 10000 - 586)
        self.assertEqual(
            [item["row_number"] for item in selected["items"]],
            [41, 47, 71],
        )
        self.assertEqual(selected["memo"], "2026/08google行銷費＋內勤薪資＋稅捐")

    def test_selected_subject_rows_require_at_least_one(self):
        plan = transfer._build_plan(_values(), "202608", "台中")
        with self.assertRaisesRegex(ValueError, "至少勾選"):
            transfer.select_plan_rows(plan, set())

    def test_bank_preflight_blocks_existing_statement_rows(self):
        plan = transfer._build_plan(_values(), "202608", "台中")
        with patch.object(
            transfer,
            "resolve_statement_location",
            side_effect=[("tp", "富邦更新"), ("tc", "富邦更新")],
        ):
            with patch.object(
                transfer,
                "_read_values",
                side_effect=[plan["taipei_rows"], []],
            ):
                with self.assertRaisesRegex(ValueError, "已有相同固定費用分錄"):
                    transfer.assert_plan_not_posted(plan)

    def test_invalid_registry_gid_does_not_use_hardcoded_fallback(self):
        with patch.object(
            transfer,
            "resolve_marketing_expense_location",
            side_effect=RuntimeError("找不到 GID=無此工作表"),
        ):
            with self.assertRaisesRegex(RuntimeError, "找不到 GID"):
                transfer._marketing_source_location()


if __name__ == "__main__":
    unittest.main()
