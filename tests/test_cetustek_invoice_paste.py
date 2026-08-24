from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import MagicMock


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
    page.fill_button = _locator()

    def locate(selector: str) -> MagicMock:
        if selector == "#lemon-ei-fill-btn":
            return page.fill_button
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
        page.locator.side_effect = lambda selector: (
            _locator() if selector == "#lemon-ei-fill-btn" else _locator(count=0, visible=False)
        )

        self.assertFalse(paste._is_invoice_create_page(page))
        with self.assertRaisesRegex(RuntimeError, "不是鯨躍發票開立頁"):
            paste._paste_button(page)

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

    def test_paste_clicks_button_in_page_and_matches_order_id(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {
            "clicked": True,
            "message": "已填入。請檢查買受人/統編、Email、付款方式、載具後再按下一步。",
        }
        payload_json = json.dumps({"orderid": "LC001"})

        paste._paste_one(page, payload_json)

        evaluate_payload = page.evaluate.call_args.args[1]
        self.assertEqual(evaluate_payload["selector"], "#lemon-ei-fill-btn")
        self.assertEqual(evaluate_payload["payload"], payload_json)
        page.expect_event.assert_not_called()

    def test_paste_rejects_false_success_when_form_order_id_is_blank(self) -> None:
        page = _invoice_page("")
        page.evaluate.return_value = {
            "clicked": True,
            "message": "已填入。請檢查後再按下一步。",
        }

        with self.assertRaisesRegex(RuntimeError, "表單驗證失敗"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_paste_rejects_tampermonkey_error_message(self) -> None:
        page = _invoice_page("LC001")
        page.evaluate.return_value = {
            "clicked": True,
            "message": "Payload JSON 格式錯誤",
        }

        with self.assertRaisesRegex(RuntimeError, "貼入結果異常"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))

    def test_extract_invoice_number_from_saved_page(self) -> None:
        page = MagicMock()
        page.locator("body").inner_text.return_value = "開立成功 發票號碼 AB-12345678"
        page.locator("input").evaluate_all.return_value = []

        self.assertEqual(paste._extract_invoice_no(page), "AB12345678")


if __name__ == "__main__":
    unittest.main()
