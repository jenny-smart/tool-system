"""鯨躍共用開立：折讓單、發票（先查已開立→開立→讀單號）。"""
from unittest.mock import MagicMock

import pytest

from tools.invoice_center import allowance_create, cetustek_invoice_paste as paste, ei_issue


@pytest.fixture
def allowance(monkeypatch):
    calls = []
    monkeypatch.setattr(paste, "_clear_dialog_handlers", lambda page: None)
    monkeypatch.setattr(allowance_create, "_open_allowance", lambda page: calls.append("open"))
    return calls


def test_allowance_reuses_existing_without_creating(monkeypatch, allowance):
    monkeypatch.setattr(allowance_create, "find_existing_allowance", lambda *a, **k: ("2026/11/09", "AL1"))
    monkeypatch.setattr(allowance_create, "_create_one", lambda *a, **k: pytest.fail("不得重開"))
    assert ei_issue.issue_allowance(MagicMock(), "ab12345678", "8000") == ("AL1", "2026/11/09", True)


def test_allowance_creates_and_passes_options(monkeypatch, allowance):
    monkeypatch.setattr(allowance_create, "find_existing_allowance", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(allowance_create, "_create_one",
                        lambda page, no, amount, **kw: seen.update(no=no, amount=amount, **kw) or "AL2")
    result = ei_issue.issue_allowance(MagicMock(), "AB12345678", "8000", invoice_year=2026, require_full=True)
    assert result.number == "AL2" and not result.reused and allowance == ["open"]
    assert seen["no"] == "AB12345678" and seen["invoice_year"] == 2026 and seen["require_full"]


def test_allowance_recovers_number_after_save_error(monkeypatch, allowance):
    found = iter([None, ("2026/11/09", "AL3")])
    monkeypatch.setattr(allowance_create, "find_existing_allowance", lambda *a, **k: next(found))
    monkeypatch.setattr(allowance_create, "_create_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("讀不到")))
    assert ei_issue.issue_allowance(MagicMock(), "AB12345678", "8000").number == "AL3"


def test_allowance_lookup_failure_never_creates(monkeypatch, allowance):
    monkeypatch.setattr(allowance_create, "find_existing_allowance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("查詢頁壞了")))
    monkeypatch.setattr(allowance_create, "_create_one", lambda *a, **k: pytest.fail("不得開立"))
    with pytest.raises(ei_issue.LookupFailed):
        ei_issue.issue_allowance(MagicMock(), "AB12345678", "8000")


@pytest.fixture
def invoice(monkeypatch):
    events = []
    monkeypatch.setattr(paste, "_open_invoice_create", lambda page: events.append("open"))
    monkeypatch.setattr(paste, "_clear_dialog_handlers", lambda page: None)
    monkeypatch.setattr(paste, "_paste_one", lambda page, data: events.append(("fill", data)))
    monkeypatch.setattr(paste, "_wait_for_manual_save",
                        lambda page, order, **kw: events.append(("wait", order, kw)) or "CD12345678")
    return events


def test_invoice_reuses_existing(monkeypatch, invoice):
    monkeypatch.setattr(paste, "find_existing_invoice", lambda *a, **k: "CD11111111")
    assert ei_issue.issue_invoice(MagicMock(), {"orderid": "LC1-1"}, lookup_order_no="LC1") == ("CD11111111", True)
    assert invoice == []


def test_invoice_fill_order_and_hooks(monkeypatch, invoice):
    monkeypatch.setattr(paste, "find_existing_invoice", lambda *a, **k: None)
    result = ei_issue.issue_invoice(MagicMock(), {"orderid": "LC1-1"}, lookup_order_no="LC1",
                                    before_fill=lambda: invoice.append("before"),
                                    after_fill=lambda: invoice.append("after"))
    assert result == ("CD12345678", False)
    assert invoice[0] == "open" and invoice[1] == "before" and invoice[2][0] == "fill" and invoice[3] == "after"
    assert invoice[4] == ("wait", "LC1", {"new_order": True})


def test_invoice_without_lookup_uses_orderid(monkeypatch, invoice):
    monkeypatch.setattr(paste, "find_existing_invoice", lambda *a, **k: pytest.fail("不查詢"))
    ei_issue.issue_invoice(MagicMock(), '{"orderid": "LC1-RAB1"}', check_existing=False)
    assert invoice[-1] == ("wait", "LC1-RAB1", {})


def test_invoice_lookup_failure_never_opens(monkeypatch, invoice):
    monkeypatch.setattr(paste, "find_existing_invoice", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(ei_issue.LookupFailed):
        ei_issue.issue_invoice(MagicMock(), {"orderid": "LC1-1"})
    assert invoice == []
