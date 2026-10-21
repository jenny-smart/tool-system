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
    # 重跑：上次已扣退款金額 17,600，只剩購物金 → 只扣剩餘餘額（人工確認）
    assert zero_out_steps(0, 2500, "17600") == [("VIP退券剩餘餘額歸零", 2500)]
    assert zero_out_steps(0, 0, "1000") == []


def test_manual_submit_waits_until_record_appears(monkeypatch):
    import pytest

    adj = pytest.importorskip("tools.lemon_backend.stored_value_adjustment")

    class Page:
        polls = 0

        def is_closed(self):
            return False

        def locator(self, selector):
            page = self

            class Body:
                def inner_text(self):
                    page.polls += 1
                    return "LC001 10/21VIP退券扣退款金額 18,000" if page.polls >= 3 else "LC001 儲值金餘額：17,600"
            return Body()

        def wait_for_timeout(self, ms):
            pass

    page = Page()
    adj._wait_manual_submit(page, "LC001", "18000", "10/21VIP退券扣退款金額")
    assert page.polls == 3
    monkeypatch.setattr(adj, "MANUAL_SUBMIT_TIMEOUT", 0)
    with pytest.raises(RuntimeError, match="逾時"):
        adj._wait_manual_submit(Page(), "LC001", "2100", "10/21VIP退券剩餘餘額歸零")


def test_zero_out_first_step_auto_rest_manual(monkeypatch):
    import pytest

    adj = pytest.importorskip("tools.lemon_backend.stored_value_adjustment")
    balances = iter([(17600, 2500), (0, 0)])
    calls = []
    monkeypatch.setattr(adj, "_balances", lambda page, order_no: next(balances))
    monkeypatch.setattr(adj, "_submit_adjustment",
                        lambda page, step, date_text, manual_submit=False: calls.append((step["amount"], manual_submit)) or True)
    item = {"order_no": "LC001983201", "note": "", "refund_amount": "17600"}
    assert adj._zero_out(object(), item, "10/21") is True
    assert calls == [("17600", False), ("2500", True)]


def test_rerun_with_only_remainder_waits_for_manual(monkeypatch):
    import pytest

    adj = pytest.importorskip("tools.lemon_backend.stored_value_adjustment")
    balances = iter([(0, 2500), (0, 0)])
    calls = []
    monkeypatch.setattr(adj, "_balances", lambda page, order_no: next(balances))
    monkeypatch.setattr(adj, "_submit_adjustment",
                        lambda page, step, date_text, manual_submit=False: calls.append((step["amount"], manual_submit)) or True)
    adj._zero_out(object(), {"order_no": "LC1", "note": "", "refund_amount": "17600"}, "10/22")
    assert calls == [("2500", True)]


def test_manual_wait_stops_when_page_closed():
    import pytest

    adj = pytest.importorskip("tools.lemon_backend.stored_value_adjustment")

    class Closed:
        def is_closed(self):
            return True

    with pytest.raises(RuntimeError, match="頁面已關閉"):
        adj._wait_manual_submit(Closed(), "LC1", "2500", "note")
    adj._logout(Closed())  # 不應拋錯
