"""清潔異動未備妥提醒：工作日 11:00／15:00、提前 30 分鐘，建在 Jenny 的行事曆。"""
from datetime import date, datetime

import pytest

from tools.orders_system import change_order_daily_check as chk

TZ = chk.TW_TZ
TODO = [{"region": "台北", "kind": "退款", "order_no": "LC1", "customer_name": "王", "missing": "折讓單", "sheet_row": 5}]
SUMMARY = {"台北": {"synced": 1, "failed": 0}}


def _at(h, m=0, d=19):
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


@pytest.mark.parametrize("now,expected", [
    (_at(9, 50), _at(11)), (_at(10, 5), _at(11)), (_at(14, 2), _at(15)), (_at(15, 1), None),
])
def test_next_slot_on_workday(now, expected):
    assert chk.next_remind_slot(now, is_workday=lambda d: True) == expected


def test_no_slot_on_holiday():
    assert chk.next_remind_slot(_at(10), is_workday=lambda d: False) is None


def test_notify_creates_event_on_jenny_calendar(monkeypatch):
    calls = []
    monkeypatch.setattr(chk, "next_remind_slot", lambda now: _at(11))
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", lambda **kw: calls.append(kw) or {"id": "e"})
    assert chk._notify(TODO, SUMMARY, now=_at(10), log=lambda *_: None) == {"id": "e"}
    kw = calls[0]
    assert kw["calendar_id"] == "jenny@lemonclean.com.tw" and kw["attendee"] == ""
    assert kw["start"] == _at(11) and kw["reminder_minutes"] == [30]
    assert "LC1" in kw["description"] and "缺：折讓單" in kw["description"]


def test_notify_failure_does_not_use_shared_calendar(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        raise PermissionError("403")

    monkeypatch.setattr(chk, "next_remind_slot", lambda now: _at(15))
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", fake)
    assert chk._notify(TODO, SUMMARY, now=_at(14), log=lambda *_: None) is None
    assert len(calls) == 1 and calls[0]["calendar_id"] == "jenny@lemonclean.com.tw"


def test_notify_skips_without_todo(monkeypatch):
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", lambda **kw: pytest.fail("不應建立"))
    assert chk._notify([], SUMMARY, now=_at(10), log=lambda *_: None) is None
