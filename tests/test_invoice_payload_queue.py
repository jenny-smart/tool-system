from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


config_module = types.ModuleType("tools.common.config_loader")
config_module.get_master_spreadsheet_id = MagicMock()
config_module.get_sheets_service = MagicMock()
sys.modules.setdefault("tools.common.config_loader", config_module)

agent_module = types.ModuleType("tools.local_agent_queue")
agent_module.now_text = MagicMock(return_value="2026-08-23 19:30:00")
sys.modules.setdefault("tools.local_agent_queue", agent_module)

change_module = types.ModuleType("tools.memo_system.change_order")
change_module.get_worksheet = MagicMock()
sys.modules.setdefault("tools.memo_system.change_order", change_module)

from tools.invoice_center import invoice_payload_queue as queue


def _sheet_row(order_no: str, invoice_no: str = "", created_at: str = "") -> list[list[str]]:
    row = [""] * 21
    row[0] = order_no
    row[8] = invoice_no
    row[20] = created_at
    return [row]


class InvoiceResultWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = MagicMock()
        queue.get_worksheet.reset_mock()
        queue.get_worksheet.return_value = self.ws

    def test_write_invoice_number_and_time_to_exact_source_row(self) -> None:
        self.ws.get.return_value = _sheet_row("LC001")

        queue.write_invoice_result("台北", 12, "LC001", "AB12345678")

        self.ws.get.assert_called_once_with("G12:AA12")
        self.ws.batch_update.assert_called_once_with([
            {"range": "O12", "values": [["AB12345678"]]},
            {"range": "AA12", "values": [["2026-08-23 19:30:00"]]},
        ])

    def test_rejects_changed_order_row(self) -> None:
        self.ws.get.return_value = _sheet_row("LC999")

        with self.assertRaisesRegex(RuntimeError, "訂單已變更"):
            queue.write_invoice_result("台北", 12, "LC001", "AB12345678")
        self.ws.batch_update.assert_not_called()

    def test_never_overwrites_other_invoice_number(self) -> None:
        self.ws.get.return_value = _sheet_row("LC001", "CD87654321")

        with self.assertRaisesRegex(RuntimeError, "禁止覆蓋"):
            queue.write_invoice_result("台北", 12, "LC001", "AB12345678")
        self.ws.batch_update.assert_not_called()

    def test_duplicate_result_preserves_existing_data(self) -> None:
        self.ws.get.return_value = _sheet_row(
            "LC001", "AB12345678", "2026-08-23 19:20:00"
        )

        queue.write_invoice_result("台北", 12, "LC001", "AB12345678")

        self.ws.batch_update.assert_not_called()

    def test_enqueue_reuses_existing_pending_without_name_error(self) -> None:
        service = MagicMock()
        existing = {"_row": 5, "area": "台北", "order_no": "LC001", "status": "pending"}

        with patch.object(queue, "get_sheets_service", return_value=service), patch.object(
            queue, "_ensure_sheet"
        ), patch.object(queue, "_all_rows", return_value=[existing]):
            self.assertEqual(queue.enqueue_payload("台北", "LC001", "{}"), 5)

    def test_list_returns_pending_and_awaiting_save(self) -> None:
        service = MagicMock()
        rows = [
            {"_row": 2, "area": "台北", "order_no": "LC001", "status": "pending"},
            {"_row": 3, "area": "台北", "order_no": "LC002", "status": "awaiting_save"},
            {"_row": 4, "area": "台北", "order_no": "LC003", "status": "completed"},
        ]

        with patch.object(queue, "get_sheets_service", return_value=service), patch.object(
            queue, "_ensure_sheet"
        ), patch.object(queue, "_all_rows", return_value=rows):
            result = queue.list_pending_payloads("台北")

        self.assertEqual([item["order_no"] for item in result], ["LC001", "LC002"])


if __name__ == "__main__":
    unittest.main()
