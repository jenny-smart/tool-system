"""VIP待退券：儲值金及購物金依頁面餘額全部歸零。"""
from tools.lemon_backend.stored_value_filter import parse_balances, pending_stored_value_adjustments, zero_out_steps


def _row(status, order_no="LC001", m="", n="", s=""):
    row = [""] * 30
    row[1], row[6], row[12], row[13], row[18] = status, order_no, m, n, s
    return row


def test_vip_rows_zero_out_until_m_filled():
    values = [[""], _row("VIP待退券", s="17600"), _row("VIP待退券", m="2026/10/21"), _row("待扣儲值金", n="500")]
    items = pending_stored_value_adjustments(values)
    assert [(i["sheet_row"], i["zero_out"]) for i in items] == [(2, True), (4, False)]
    vip = items[0]
    assert vip["amount"] == "全部歸零" and vip["action"] == "扣款" and vip["refund_amount"] == "17600"
    assert vip["time_column"] == 13 and vip["completed_status"] == "VIP待退券"


def test_parse_balances_from_history_page():
    text = "異動儲值金\n儲值金餘額：17,600\n購物金餘額：2,500\n購物金期限： 2,500(有效期限2027-11-05)\n儲值金及購物金餘額 備註"
    assert parse_balances(text) == (17600, 2500)
    assert parse_balances("儲值金餘額：0\n購物金餘額：0") == (0, 0)
    assert parse_balances("儲值金及購物金餘額") is None


def test_zero_out_steps():
    # 退款金額大於儲值金：先扣退款金額（不足由購物金扣），再扣剩下的餘額
    assert zero_out_steps(17600, 2500, "18000") == [("VIP退券扣退款金額", 18000), ("VIP退券剩餘餘額歸零", 2100)]
    assert zero_out_steps(17600, 2500, "20100") == [("VIP退券扣退款金額", 20100)]
    assert zero_out_steps(17600, 2500, "25000") == [("VIP退券扣退款金額", 20100)]  # 最多扣到 0
    # 退款金額不大於儲值金：儲值金、購物金各扣一筆
    assert zero_out_steps(17600, 2500, "17600") == [("VIP退券儲值金歸零", 17600), ("VIP退券剩餘餘額歸零", 2500)]
    assert zero_out_steps(17600, 0, "15000") == [("VIP退券儲值金歸零", 17600)]
    assert zero_out_steps(0, 2500, "") == [("VIP退券剩餘餘額歸零", 2500)]
    assert zero_out_steps(0, 0, "1000") == []
