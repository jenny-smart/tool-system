from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tools.invoice_center.bridge import fetch_backend_order_invoice_payload
from tools.lemon_backend.models import BackendOrder
from tools.lemon_backend.orders import (
    hydrate_order_from_edit_page,
    search_paid_stored_value_orders_by_phone,
)


def _order(**overrides) -> BackendOrder:
    values = {
        "order_no": "LC100",
        "customer_name": "客戶",
        "phone": "0912345678",
        "email": "member@example.com",
        "address": "台北市",
        "amount": "200",
        "payway": "儲值金",
        "paid_status": "已付款",
        "items": ["居家清潔"],
        "extra": {},
    }
    values.update(overrides)
    return BackendOrder(**values)


class InvoiceSourceSelectionTest(unittest.TestCase):
    def test_edit_page_member_carrier_clears_stale_company_fields(self) -> None:
        order = _order(
            buyer_identifier="19920908",
            buyer_name="錯誤公司",
            invoice_type="三聯式",
            carrier_type="紙本",
            edit_url="/purchase/edit/1",
        )
        response = SimpleNamespace(
            text="""
                <form>
                  <input name="invoice_type" value="2">
                  <input name="carrier_type_id" value="1">
                  <input name="carrier_info" value="member@example.com">
                  <input name="company_no" value="19920908">
                </form>
            """,
            raise_for_status=lambda: None,
        )
        session = MagicMock()
        session.get.return_value = response

        result = hydrate_order_from_edit_page(session, order)

        self.assertEqual(result.invoice_type, "二聯式")
        self.assertEqual(result.buyer_identifier, "")
        self.assertEqual(result.buyer_name, "")
        self.assertEqual(result.carrier_type, "會員載具")
        self.assertEqual(result.carrier_no, "member@example.com")

    def test_stored_value_payment_uses_newest_paid_stored_value_purchase(self) -> None:
        current = _order(order_no="LC200")
        older = _order(
            order_no="LC150",
            items=["儲值金-台北"],
            buyer_identifier="11111111",
            buyer_name="舊公司",
            carrier_type="紙本",
            extra={"paid_at": "2026-01-01 10:00:00"},
        )
        newest = _order(
            order_no="LC180",
            items=["儲值金-台北(儲值金50,000)"],
            buyer_identifier="70450942",
            buyer_name="",
            invoice_type="三聯式",
            carrier_type="紙本",
            extra={
                "paid_at": "2026-03-16 12:31:49",
                "company_title": "娜亞國際股份有限公司",
            },
        )
        unpaid = _order(
            order_no="LC190",
            items=["儲值金-台北"],
            paid_status="待付款",
            extra={"paid_at": "2026-04-01 10:00:00"},
        )
        client = MagicMock()
        client.get_order.return_value = current
        client.search_paid_stored_value_orders_by_phone.return_value = [
            unpaid,
            older,
            newest,
        ]

        order, payload = fetch_backend_order_invoice_payload(
            "台北", "LC200", backend_client=client
        )

        client.search_paid_stored_value_orders_by_phone.assert_called_once_with("0912345678")
        self.assertEqual(order.buyer_identifier, "70450942")
        self.assertEqual(order.buyer_name, "娜亞國際股份有限公司")
        self.assertEqual(order.extra["invoice_settings_source_order"], "LC180")
        self.assertEqual(payload.buyer_identifier, "70450942")
        self.assertEqual(payload.buyer_name, "娜亞國際股份有限公司")

    def test_stored_value_history_uses_backend_buy_and_paid_filters(self) -> None:
        response = SimpleNamespace(
            text='purchaseList: {"data": []}',
            raise_for_status=lambda: None,
        )
        session = MagicMock()
        session.base_url = "https://backend.lemonclean.com.tw"
        session.get.return_value = response

        self.assertEqual(
            search_paid_stored_value_orders_by_phone(session, "912-345-678"),
            [],
        )

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["phone"], "0912345678")
        self.assertEqual(params["buy"], "5")
        self.assertEqual(params["purchase_status"], "1")

    def test_non_stored_value_payment_keeps_current_invoice_settings(self) -> None:
        current = _order(
            payway="信用卡",
            buyer_identifier="",
            buyer_name="",
            carrier_type="會員載具",
            carrier_no="member@example.com",
        )
        client = MagicMock()
        client.get_order.return_value = current

        order, payload = fetch_backend_order_invoice_payload(
            "台北", "LC100", backend_client=client
        )

        client.search_paid_stored_value_orders_by_phone.assert_not_called()
        self.assertEqual(order.buyer_identifier, "")
        self.assertEqual(payload.carrierid1, "member@example.com")


if __name__ == "__main__":
    unittest.main()
