"""2026-11-21 台北第 374 列 LC00221084：J 欄「合併服務」不是合併開立發票，回寫不可要求 O 欄。"""
from tools.invoice_center.merged import merged_order_numbers
from tools.orders_system.memo_system import change_order

J_SERVICE = "11/20(五)異動：訂單轉換 LC00221084+LC00221086 合併服務，新訂單合計少於原訂單，退費 $300"
J_INVOICE = "合併開立發票$10800（LC00221068、LC00221054）"


def test_merged_service_is_not_merged_invoice():
    for fn in (merged_order_numbers, change_order.merged_order_numbers):
        assert fn("LC00221084", J_SERVICE, "") == ["LC00221084"]
        assert fn("LC00221068", J_INVOICE, "") == ["LC00221068", "LC00221054"]
        assert fn("LC00221068 LC00221054", "", "") == ["LC00221068", "LC00221054"]
