from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


queue_module = types.ModuleType("tools.invoice_center.invoice_payload_queue")
queue_module.list_pending_payloads = MagicMock()
queue_module.update_payload_status = MagicMock()
queue_module.write_invoice_result = MagicMock()
sys.modules.setdefault("tools.invoice_center.invoice_payload_queue", queue_module)

from tools.invoice_center import cetustek_invoice_paste as paste


def _locator(count: int = 1, *, visible: bool = True, value: str = "") -> MagicMock:
    locator = MagicMock()
    locator.count.return_value = count
    locator.first = locator
    locator.is_visible.return_value = visible
    locator.input_value.return_value = value
    return locator


def _invoice_page(order_id: str = "LC001") -> MagicMock:
    page = MagicMock()
    page.url = paste.INVOICE_CREATE_URL

    def locate(selector: str) -> MagicMock:
        if selector == "#orderid":
            return _locator(value=order_id)
        if selector in paste.INVOICE_FORM_SELECTORS:
            return _locator()
        return _locator(count=0, visible=False)

    page.locator.side_effect = locate
    return page


class CetustekInvoicePasteTest(unittest.TestCase):
    def test_helper_button_alone_is_not_invoice_page(self) -> None:
        page = MagicMock()
        page.locator.return_value = _locator(count=0, visible=False)
        self.assertFalse(paste._is_invoice_create_page(page))

    def test_native_invoice_fields_identify_invoice_page(self) -> None:
        self.assertTrue(paste._is_invoice_create_page(_invoice_page()))

    def test_native_fields_on_wrong_url_are_not_invoice_page(self) -> None:
        page = _invoice_page()
        page.url = "https://www.ei.com.tw/InvoiceRent/index.jsp"

        self.assertFalse(paste._is_invoice_create_page(page))

    def test_open_invoice_create_uses_fixed_url(self) -> None:
        page = _invoice_page()
        page.url = "https://www.ei.com.tw/InvoiceRent/index.jsp"

        def goto(url: str, **_: object) -> None:
            page.url = url

        page.goto.side_effect = goto
        paste._open_invoice_create(page)

        page.goto.assert_called_once_with(
            paste.INVOICE_CREATE_URL,
            wait_until="domcontentloaded",
            timeout=15000,
        )

    def test_paste_fills_native_form_and_matches_order_id(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {"ok": True, "message": "已直接填入鯨躍原生表單"}
        payload = {"orderid": "LC001"}
        paste._paste_one(page, json.dumps(payload))

        self.assertEqual(page.evaluate.call_args.args[1], payload)
        self.assertIn("setValue", page.evaluate.call_args.args[0])
        self.assertIn(
            'forceRadio(isTriplicate ? "hastax1"',
            page.evaluate.call_args.args[0],
        )
        page.expect_event.assert_not_called()

    def test_paste_rejects_false_success_when_form_order_id_is_blank(self) -> None:
        page = _invoice_page("")
        page.evaluate.return_value = {"ok": True, "message": "已直接填入鯨躍原生表單"}

        with self.assertRaisesRegex(RuntimeError, "表單驗證失敗"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_paste_rejects_direct_fill_error(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {"ok": False, "message": "缺少鯨躍欄位：totalamount"}

        with self.assertRaisesRegex(RuntimeError, "填入結果異常"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_wait_for_save_accepts_new_invoice_number_without_click_marker(self) -> None:
        page = MagicMock()
        page.url = paste.INVOICE_CREATE_URL
        page.evaluate.return_value = False
        page.locator("body").inner_text.side_effect = [
            "",
            "開立成功 發票號碼 AB-12345678",
        ]
        page.locator("input").evaluate_all.return_value = []

        with patch.object(
            paste,
            "_extract_invoice_no_for_order",
            side_effect=["", "AB12345678"],
        ):
            self.assertEqual(
                paste._wait_for_manual_save(page, "LC001", timeout_ms=1000),
                "AB12345678",
            )
        page.bring_to_front.assert_called_once()

    def test_existing_query_result_is_recovered_without_opening_new_invoice(self) -> None:
        page = MagicMock()
        item = {
            "_row": 7,
            "source_row": 12,
            "order_no": "LC002146661",
            "payload_json": json.dumps({"orderid": "LC002146661"}),
        }
        paste.list_pending_payloads.return_value = [item]

        with patch.object(
            paste,
            "_extract_invoice_no_for_order",
            return_value="DM51790871",
        ), patch.object(paste, "_open_invoice_create") as open_invoice:
            self.assertEqual(paste.process_pending_invoice_payloads(page, "台北"), 1)

        open_invoice.assert_not_called()
        paste.write_invoice_result.assert_called_once_with(
            "台北", 12, "LC002146661", "DM51790871"
        )
        paste.update_payload_status.assert_called_with(
            7,
            "completed",
            "已從查詢頁復原並回填 O/AA：DM51790871",
        )

    def test_awaiting_save_without_query_result_never_opens_new_invoice(self) -> None:
        page = MagicMock()
        item = {
            "_row": 8,
            "source_row": 13,
            "order_no": "LC002145591",
            "payload_json": json.dumps({"orderid": "LC002145591"}),
            "status": "awaiting_save",
        }
        paste.list_pending_payloads.return_value = [item]

        with patch.object(
            paste,
            "_extract_invoice_no_for_order",
            return_value="",
        ), patch.object(paste, "_open_invoice_create") as open_invoice:
            with self.assertRaisesRegex(RuntimeError, "為避免重複開立"):
                paste.process_pending_invoice_payloads(page, "台北")

        open_invoice.assert_not_called()

    def test_processes_all_pending_in_sequence(self) -> None:
        page = MagicMock()
        pending = [
            {
                "_row": 7,
                "source_row": 12,
                "order_no": "LC001",
                "payload_json": json.dumps({"orderid": "LC001"}),
                "status": "pending",
            },
            {
                "_row": 8,
                "source_row": 13,
                "order_no": "LC002",
                "payload_json": json.dumps({"orderid": "LC002"}),
                "status": "pending",
            },
        ]

        with patch.object(paste, "list_pending_payloads", return_value=pending), patch.object(
            paste, "_extract_invoice_no_for_order", return_value=""
        ), patch.object(paste, "_open_invoice_create") as open_invoice, patch.object(
            paste, "_clear_dialog_handlers"
        ), patch.object(paste, "_paste_one") as paste_one, patch.object(
            paste,
            "_wait_for_manual_save",
            side_effect=["AB12345678", "AB12345679"],
        ), patch.object(paste, "write_invoice_result") as write_result, patch.object(
            paste, "update_payload_status"
        ):
            self.assertEqual(paste.process_pending_invoice_payloads(page, "台北"), 2)

        self.assertEqual(open_invoice.call_count, 2)
        self.assertEqual(paste_one.call_count, 2)
        self.assertEqual(write_result.call_count, 2)

    def test_extract_invoice_number_from_matching_order_row(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = [
            "DM51791827 LC002147991",
            "DM51790871 LC002146661-\n1",
        ]

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )

    def test_extract_invoice_number_from_enriched_value_fields(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661 DM51790871"],
            "page": "LC002146661 DM51790871",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )
        evaluated_script = page.evaluate.call_args.args[0]
        self.assertIn("input, a", evaluated_script)
        self.assertIn("data-orderid", evaluated_script)

    def test_extract_separate_dom_fields_only_when_invoice_is_unique(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661", "DM51790871"],
            "page": "LC002146661 DM51790871",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "DM51790871",
        )

    def test_extract_separate_dom_fields_rejects_multiple_invoices(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC002146661", "DM51790871", "DM51791827"],
            "page": "LC002146661 DM51790871 DM51791827",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "",
        )

    def test_maps_ei_column_four_order_to_column_two_invoice(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": [{
                "text": "115/08/25 DM51790873 115/08/25 LC00213133-1",
                "columns": [
                    "115/08/25",
                    "DM51790873",
                    "115/08/25",
                    "LC00213133-1",
                ],
            }],
            "page": "115/08/25 DM51790873 115/08/25 LC00213133-1",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "DM51790873",
        )

    def test_does_not_take_invoice_from_row_with_other_column_four_order(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": [{
                "text": "115/08/25 DM51790872 115/08/25 LC002145591-1",
                "columns": [
                    "115/08/25",
                    "DM51790872",
                    "115/08/25",
                    "LC002145591-1",
                ],
            }],
            "page": "",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "",
        )

    def test_order_shaped_like_invoice_is_excluded(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC00213133-1 DM51790873"],
            "page": "LC00213133-1 DM51790873",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "DM51790873",
        )

    def test_order_without_invoice_is_not_returned_as_invoice(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = {
            "rows": ["LC00213133-1"],
            "page": "LC00213133-1",
        }

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC00213133"),
            "",
        )

    def test_extract_invoice_number_does_not_match_similar_order(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = ["DM51791827 LC0021466619-1"]

        self.assertEqual(
            paste._extract_invoice_no_for_order(page, "LC002146661"),
            "",
        )

    def test_extract_invoice_number_from_saved_page(self) -> None:
        page = MagicMock()
        page.locator("body").inner_text.return_value = "開立成功 發票號碼 AB-12345678"
        page.locator("input").evaluate_all.return_value = []

        self.assertEqual(paste._extract_invoice_no(page), "AB12345678")


if __name__ == "__main__":
    unittest.main()
