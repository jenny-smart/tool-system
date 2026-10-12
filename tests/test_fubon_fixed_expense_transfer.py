import sys
import unittest
from unittest.mock import MagicMock, patch

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

    def test_bank_transfer_requires_blank_row_74(self):
        transfer.require_fubon_not_transferred(
            {"transfer_mark": ""}, "202608", "台中"
        )
        for value in ("已匯款", "2026/10/13"):
            with self.assertRaisesRegex(ValueError, "避免重複匯款"):
                transfer.require_fubon_not_transferred(
                    {"transfer_mark": value}, "202608", "台中"
                )

    def test_post_payment_runs_download_and_posting_before_row_74_marker(self):
        events = []
        plan = {"total": 36112}
        page = MagicMock()
        context = MagicMock()
        browser = MagicMock()
        playwright = MagicMock()
        playwright.__enter__.return_value = MagicMock()
        with patch.object(fubon_transfer, "load_transfer_plan", return_value=plan), patch.object(
            fubon_transfer, "require_fubon_not_transferred"
        ), patch.object(fubon_transfer, "assert_plan_not_posted"), patch.object(
            fubon_transfer, "load_account", return_value=MagicMock(bank_account="123")
        ), patch.object(fubon_transfer, "sync_playwright", return_value=playwright), patch.object(
            fubon_transfer, "connect_existing_chrome", return_value=(browser, context)
        ), patch.object(fubon_transfer, "ensure_login", return_value=page), patch.object(
            fubon_transfer, "fill_fixed_expense_transfer"
        ), patch.object(
            fubon_transfer,
            "wait_user_completed_transfer",
            return_value="2026-10-13 05:43:35",
        ), patch.object(
            fubon_transfer,
            "run_download",
            side_effect=lambda *args: events.append("download"),
        ), patch.object(
            fubon_transfer,
            "transfer_fixed_expenses",
            side_effect=lambda *args, **kwargs: events.append("posting") or {"summaries": []},
        ), patch.object(
            fubon_transfer,
            "write_transfer_date",
            side_effect=lambda *args: events.append("marker") or "F74",
        ):
            result = fubon_transfer.run("台中", "202608", MagicMock(), "cdp")

        self.assertEqual(result, 0)
        self.assertEqual(events, ["download", "posting", "marker"])


if __name__ == "__main__":
    unittest.main()
