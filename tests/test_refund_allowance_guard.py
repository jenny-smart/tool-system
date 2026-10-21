"""折讓單與退款成對：開立前查已開未回填、開立後讀不到單號再查、退款前缺折讓單號就跳過。"""
from unittest.mock import MagicMock

import pytest

from tools.finance_management import refund_guard

allowance = pytest.importorskip("tools.invoice_center.allowance_create")


def _row(x="", ab=""):
    row = [""] * 30
    row[23], row[27] = x, ab
    return row


def test_refund_requires_allowance_number_when_invoice_exists():
    values = [_row(), _row("FP56436829", ""), _row("FP56436854", "FP90000001"), _row("", "")]
    assert refund_guard.missing_allowance(values, 2)
    assert not refund_guard.missing_allowance(values, 3)
    assert not refund_guard.missing_allowance(values, 4)  # 沒開發票不需要折讓單
    sheet = MagicMock()
    sheet.get_all_values.return_value = values
    messages = []
    assert refund_guard.ready_for_refund(sheet, 2, messages.append) is False
    assert "未退款" in messages[0]
    assert refund_guard.ready_for_refund(sheet, 3) is True


ROWS = [
    "1 115/09/02 FP56436829 FP90000001 黃傑 952 47 已開立",          # 以前別次的折讓（登記日前）
    "2 115/10/19 FP56436829 FP90000077 黃傑 500 25 已開立",          # 金額不符
    "3 115/10/19 FP56436829 FP90000099 黃傑 1,333 67 已開立",        # 這一筆
]


def test_parse_finds_this_allowance_only():
    assert allowance.parse_allowance_rows(ROWS, "FP56436829", untaxed="1333.333", not_before="2026/10/19") == (
        "2026/10/19", "FP90000099")


def test_parse_ignores_voided_and_other_invoices():
    rows = ["1 115/10/19 FP56436829 FP90000099 1,333 已作廢", "2 115/10/19 FP11111111 FP90000100 1,333"]
    assert allowance.parse_allowance_rows(rows, "FP56436829", untaxed="1333.333", not_before="2026/10/19") is None


def test_ready_items_drops_rows_without_allowance_before_login():
    values = [_row(), _row("FP56436829", ""), _row("FP56436854", "FP90000001"), _row("", "")]
    items = [{"sheet_row": 2}, {"sheet_row": 3}, {"sheet_row": 4}]
    messages = []
    ready = refund_guard.ready_items(values, items, messages.append)
    assert [item["sheet_row"] for item in ready] == [3, 4]
    assert "第 2 列" in messages[0]


def test_newebpay_refund_skips_login_when_no_allowance(monkeypatch):
    refund_pending = pytest.importorskip("tools.newebpay.refund_pending")
    sheet = MagicMock()
    sheet.get_all_values.return_value = [_row(), _row("FP56436829", "")]
    monkeypatch.setattr(refund_pending, "get_worksheet", lambda area: sheet)
    monkeypatch.setattr(refund_pending, "pending_credit_card_refunds",
                        lambda values: [{"sheet_row": 2, "order_no": "A1", "amount": "100"}])
    monkeypatch.setattr(refund_pending, "load_accounts", lambda *a: pytest.fail("不應讀帳號"))
    monkeypatch.setattr(refund_pending, "sync_playwright", lambda: pytest.fail("不應開瀏覽器登入"))
    assert refund_pending.run("台北", MagicMock(), "http://x", {2}) == 0


def _vip(m="", x="", ab=""):
    row = _row(x, ab)
    row[1], row[12] = "VIP待退券", m
    return row


def test_vip_refund_waits_for_stored_value_zeroed():
    values = [_row(), _vip(), _vip("2026/10/21"), _vip("2026/10/21", "FP1", "")]
    messages = []
    ready = refund_guard.ready_items(values, [{"sheet_row": 2}, {"sheet_row": 3}, {"sheet_row": 4}], messages.append)
    assert [item["sheet_row"] for item in ready] == [3]
    assert "歸零" in messages[0] and "折讓單號" in messages[1]
    assert refund_guard.refunded_status("VIP待退券") == "VIP已退券"
    assert refund_guard.refunded_status("待退款") == "已退款"
