"""【檸檬後台】異動儲值金雲端版：先盤點（算出歸零金額）、執行前重讀餘額。"""
from contextlib import contextmanager

import pytest

from tools.lemon_backend import stored_value_adjustment as sva

HEADER = [""] * 30


def _row(status, order, amount_col=None, amount="", note="備註", s_refund=""):
    row = [""] * 30
    row[1], row[6], row[10] = status, order, note
    if amount_col is not None:
        row[amount_col] = amount
    row[18] = s_refund or row[18]
    return row


class FakeSheet:
    def __init__(self, values):
        self.values = values
        self.updates = []

    def get_all_values(self):
        return self.values

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


@pytest.fixture
def backend(monkeypatch):
    state = {"balances": {"V1": (17600, 2500)}, "submitted": []}
    sheet = FakeSheet([HEADER, _row("待扣儲值金", "A1", 13, "1,200"), _row("VIP待退券", "V1", s_refund="")])
    monkeypatch.setattr(sva, "get_worksheet", lambda area: sheet)

    @contextmanager
    def page():
        yield object()

    monkeypatch.setattr(sva, "_headless_page", page)
    monkeypatch.setattr(sva, "_login", lambda page, area: None)
    monkeypatch.setattr(sva, "_logout", lambda page, context=None: None)
    monkeypatch.setattr(sva, "_open_history", lambda page, order_no: order_no)
    monkeypatch.setattr(sva, "_balances", lambda page, order_no: state["balances"][order_no])

    def submit(page, item, date_text, manual_submit=False):
        assert manual_submit is False          # 雲端版一律自動送出（金額已在盤點確認）
        state["submitted"].append((item["order_no"], int(item["amount"]), item["suffix"]))
        if item["order_no"] in state["balances"]:
            stored, shopping = state["balances"][item["order_no"]]
            left = stored + shopping - int(item["amount"])
            state["balances"][item["order_no"]] = (max(left - shopping, 0), min(shopping, left))
        return True

    monkeypatch.setattr(sva, "_submit_adjustment", submit)
    return state, sheet


def test_plan_lists_exact_zero_out_amounts(backend):
    state, _sheet = backend
    planned = sva.plan_adjustments("台北", log=lambda m: None)
    by_order = {p["order_no"]: p for p in planned}
    assert by_order["A1"]["steps"] == [["已扣", 1200]]
    assert by_order["V1"]["balances"] == [17600, 2500]
    assert by_order["V1"]["steps"] == [["VIP退券儲值金歸零", 17600], [sva.REMAINDER_SUFFIX, 2500]]
    assert state["submitted"] == []           # 盤點不送出


def test_execute_submits_confirmed_steps_and_writes_back(backend):
    state, sheet = backend
    planned = sva.plan_adjustments("台北", log=lambda m: None)
    result = sva.execute_plan("台北", planned, log=lambda m: None)
    assert result == {"done": 2, "skipped": 0, "failed": 0}
    assert ("V1", 2500, sva.REMAINDER_SUFFIX) in state["submitted"]
    assert {(r, c) for r, c, _v in sheet.updates if c == 2} == {(2, 2), (3, 2)}


def test_execute_skips_when_balance_changed_since_scan(backend):
    state, _sheet = backend
    planned = sva.plan_adjustments("台北", log=lambda m: None)
    state["balances"]["V1"] = (17600, 3000)    # 盤點後購物金變了
    messages = []
    result = sva.execute_plan("台北", planned, log=messages.append)
    assert result == {"done": 1, "skipped": 1, "failed": 0}
    assert all(order != "V1" for order, _a, _s in state["submitted"])
    assert any("餘額已變動" in m for m in messages)


def test_execute_skips_rows_no_longer_pending(backend):
    state, sheet = backend
    planned = sva.plan_adjustments("台北", log=lambda m: None)
    sheet.values[1][1] = "已扣儲值金"           # 別人先處理掉了
    result = sva.execute_plan("台北", planned, log=lambda m: None)
    assert result["skipped"] == 1 and all(o != "A1" for o, _a, _s in state["submitted"])
