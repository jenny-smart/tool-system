"""藍新退款防重複：退款前查明細已有退款紀錄就不退；送出後立刻寫 AC；訂單編號完整比對。"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

refund_pending = pytest.importorskip("tools.newebpay.refund_pending")


def test_order_pattern_matches_whole_order_number_only():
    pattern = refund_pending.order_pattern("LC00220455")
    assert pattern.search("26111310 LC00220455 MS32863064")
    assert not pattern.search("26111310 LC002204551 MS32863064")
    assert not pattern.search("XLC00220455")


def test_refund_history_finds_refunds_and_ignores_cancelled():
    # 藍新 LC00220455 交易歷程記錄（2026-11-14 實際畫面）
    rows = [
        "交易歷程記錄",
        "日期\t執行狀態\t金額\t預計撥款日\t執行IP",
        "2026-11-12 20:59:00\t請款(成功)\tNT$ 9,000\t2026-11-17\t-",
        "2026-11-13 10:35:32\t退款(成功)\tNT$ 1,800\t2026-11-18\t111.250.107.80",
        "2026-11-14 18:44:33\n(2026-11-14 18:58:24)\t退款\n(取消)\tNT$ 1,800\t0000-00-00\t118.233.4.135",
    ]
    assert [when for when, _ in refund_pending.refund_history(rows)] == ["2026-11-13 10:35:32"]
    assert refund_pending.refund_history(rows[:3]) == []
    assert refund_pending.refund_history(["2026-11-14 10:00:00\t退款(申請中)\tNT$ 600"])


def _row(order_no="LC00220455"):
    row = [""] * 30
    row[1], row[6], row[17], row[18] = "待退款", order_no, "信用卡", "1800"
    return row


@pytest.fixture
def harness(monkeypatch):
    sheet = MagicMock()
    sheet.get_all_values.return_value = [[""] * 30, _row()]
    monkeypatch.setattr(refund_pending, "get_worksheet", lambda area: sheet)
    monkeypatch.setattr(refund_pending, "load_accounts", lambda *a: [MagicMock()])
    monkeypatch.setattr(refund_pending, "login", lambda *a: None)
    monkeypatch.setattr(refund_pending, "logout", lambda *a: None)
    monkeypatch.setattr(refund_pending, "_set_order_query", lambda *a: None)
    monkeypatch.setattr(refund_pending, "connect_existing_chrome", lambda pw, url: (None, MagicMock()))
    page = MagicMock()
    page.url = "https://www.newebpay.com/"
    page.is_closed.return_value = True
    monkeypatch.setattr(refund_pending, "find_existing_page", lambda *a: page)

    @contextmanager
    def fake_playwright():
        yield MagicMock()

    monkeypatch.setattr(refund_pending, "sync_playwright", fake_playwright)
    submitted = []
    monkeypatch.setattr(refund_pending, "_submit_refund", lambda page, order_no, amount: submitted.append(order_no))
    return sheet, submitted


def _ranges(sheet):
    return {call.kwargs["range_name"]: call.kwargs["values"][0][0] for call in sheet.update.call_args_list}


def test_skips_when_newebpay_already_has_refund(harness, monkeypatch, capsys):
    sheet, submitted = harness
    monkeypatch.setattr(refund_pending, "_existing_refunds",
                        lambda page, order_no: [("2026-11-13 10:27:05", "退款(申請中) 2026-11-13 10:27:05")])
    assert refund_pending.run("台北", MagicMock(), "http://x", {2}) == 0
    assert submitted == []
    assert sheet.update.call_count == 0
    assert "已有退款紀錄" in capsys.readouterr().out


def test_skips_when_detail_cannot_be_read(harness, monkeypatch):
    sheet, submitted = harness

    def broken(page, order_no):
        raise RuntimeError("讀不到藍新交易明細")

    monkeypatch.setattr(refund_pending, "_existing_refunds", broken)
    refund_pending.run("台北", MagicMock(), "http://x", {2})
    assert submitted == [] and sheet.update.call_count == 0


def test_writes_ac_right_after_submit_even_if_time_lookup_fails(harness, monkeypatch):
    sheet, submitted = harness
    calls = iter([[], RuntimeError("明細沒載入")])

    def existing(page, order_no):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(refund_pending, "_existing_refunds", existing)
    monkeypatch.setattr(refund_pending, "_now_text", lambda: "2026-11-14 18:44:30")
    refund_pending.run("台北", MagicMock(), "http://x", {2})
    assert submitted == ["LC00220455"]
    assert _ranges(sheet) == {"AC2": "2026-11-14 18:44:30", "B2": "已退款"}


def test_overwrites_ac_with_newebpay_time(harness, monkeypatch):
    sheet, submitted = harness
    calls = iter([[], [("2026-11-14 18:44:33", "退款(申請中) 2026-11-14 18:44:33")]])
    monkeypatch.setattr(refund_pending, "_existing_refunds", lambda page, order_no: next(calls))
    monkeypatch.setattr(refund_pending, "_now_text", lambda: "2026-11-14 18:44:30")
    refund_pending.run("台北", MagicMock(), "http://x", {2})
    assert [c.kwargs["range_name"] for c in sheet.update.call_args_list] == ["AC2", "B2", "AC2"]
    assert _ranges(sheet)["AC2"] == "2026-11-14 18:44:33"
