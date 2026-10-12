import sys
import unittest
from unittest.mock import patch

from tools import local_agent
from tools.bank_statement import fubon_fixed_expense_transfer as fubon_transfer
from tools.finance_management import fixed_expense_transfer as transfer


class FubonFixedExpenseTransferTest(unittest.TestCase):
    def test_local_agent_command_includes_selected_subject_rows(self):
        command = local_agent.build_fubon_fixed_expense_transfer(
            {
                "area": "台中",
                "month": "202608",
                "selected_rows": [41, 47, 71],
                "cdp_url": "http://127.0.0.1:9222",
            }
        )

        self.assertEqual(command[0], sys.executable)
        self.assertIn("tools.bank_statement.fubon_fixed_expense_transfer", command)
        self.assertEqual(command[command.index("--rows") + 1], "41,47,71")

    def test_local_agent_rejects_all_area(self):
        with self.assertRaisesRegex(ValueError, "單一地區"):
            local_agent.build_fubon_fixed_expense_transfer(
                {"area": "全區", "month": "202608"}
            )

    def test_load_plan_works_when_row_74_is_blank(self):
        plan = {
            "area": "台中",
            "report_area": "台中",
            "total": 100,
            "memo": "2026/08google行銷費",
            "items": [{"row_number": 41, "subject": "google行銷費", "amount": 100}],
            "taipei_rows": [[False, "", "", "", -100, "", "", "", "google行銷費", "", "", "label"]],
            "area_rows": [[False, "", "", "", 100, "", "", "", "google行銷費", "", "", "label"]],
            "eligible": False,
        }
        with patch.object(
            fubon_transfer,
            "preview_fixed_expenses",
            return_value={"plans": [plan]},
        ):
            selected = fubon_transfer.load_transfer_plan("202608", "台中", {41})

        self.assertEqual(selected["total"], 100)
        self.assertFalse(selected["eligible"])

    def test_bank_transfer_requires_nonblank_row_74(self):
        with self.assertRaisesRegex(ValueError, "第74列空白"):
            transfer.require_fubon_transfer_mark(
                {"transfer_mark": ""}, "202608", "台中"
            )

        transfer.require_fubon_transfer_mark(
            {"transfer_mark": "待匯款"}, "202608", "台中"
        )
        with self.assertRaisesRegex(ValueError, "避免重複匯款"):
            transfer.require_fubon_transfer_mark(
                {"transfer_mark": "2026/10/13"}, "202608", "台中"
            )


if __name__ == "__main__":
    unittest.main()
