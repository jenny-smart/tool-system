from tools.lemon_backend.stored_value_filter import pending_stored_value_adjustments


def _change_order_row(status: str) -> list[str]:
    row = [""] * 29
    row[1] = status  # B
    row[6] = "LC123456"  # G
    row[10] = "測試備註"  # K
    return row


def test_deduct_stored_value_uses_m_collection_time() -> None:
    row = _change_order_row("待扣儲值金")
    row[12] = "2026/08/25 10:00:00"  # M 收款時間
    row[13] = "1200"  # N 扣款金額
    row[28] = "不得讀取此欄"  # AC 退款時間

    item = pending_stored_value_adjustments([["標題"], row])[0]

    assert item["completed_status"] == "已扣儲值金"
    assert item["time_column"] == 13
    assert item["completed_at"] == "2026/08/25 10:00:00"


def test_return_stored_value_uses_ac_refund_time() -> None:
    row = _change_order_row("待返儲值金")
    row[12] = "不得讀取此欄"  # M 收款時間
    row[18] = "800"  # S 返款金額
    row[28] = "2026/08/25 11:00:00"  # AC 退款時間

    item = pending_stored_value_adjustments([["標題"], row])[0]

    assert item["completed_status"] == "已返儲值金"
    assert item["time_column"] == 29
    assert item["completed_at"] == "2026/08/25 11:00:00"
