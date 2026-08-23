from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import MagicMock


queue_module = types.ModuleType("tools.invoice_center.invoice_payload_queue")
queue_module.list_pending_payloads = MagicMock()
queue_module.update_payload_status = MagicMock()
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

    def locate(selector: str) -> MagicMock:
        if selector == "#lemon-ei-fill-btn":
            return _locator()
        if selector == "#orderid":
            return _locator(value=order_id)
        if selector in paste.INVOICE_FORM_SELECTORS:
            return _locator()
        return _locator(count=0, visible=False)

    page.locator.side_effect = locate
    return page


def _dialog_context(dialog: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__enter__.return_value = context
    context.__exit__.return_value = False
    context.value = dialog
    return context


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

    def test_paste_requires_success_dialog_and_matching_order_id(self) -> None:
        page = _invoice_page("LC001")
        prompt = MagicMock(type="prompt")
        confirmation = MagicMock(
            message="已填入。請檢查買受人/統編、Email、付款方式、載具後再按下一步。"
        )
        page.expect_dialog.side_effect = [
            _dialog_context(prompt),
            _dialog_context(confirmation),
        ]

        paste._paste_one(page, json.dumps({"orderid": "LC001"}))

        prompt.accept.assert_called_once()
        confirmation.accept.assert_called_once()

    def test_paste_rejects_false_success_when_form_order_id_is_blank(self) -> None:
        page = _invoice_page("")
        prompt = MagicMock(type="prompt")
        confirmation = MagicMock(message="已填入。請檢查後再按下一步。")
        page.expect_dialog.side_effect = [
            _dialog_context(prompt),
            _dialog_context(confirmation),
        ]

        with self.assertRaisesRegex(RuntimeError, "表單驗證失敗"):
            paste._paste_one(page, json.dumps({"orderid": "LC001"}))


if __name__ == "__main__":
    unittest.main()
