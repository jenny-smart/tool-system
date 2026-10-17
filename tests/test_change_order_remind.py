"""清潔異動未備妥提醒：工作日 11:00、提前 30 分鐘（10:30），建在 Jenny 的行事曆；每日檢查不回寫系統。"""
from datetime import datetime

import pytest

from tools.orders_system import change_order_daily_check as chk

TZ = chk.TW_TZ
TODO = [{"region": "台北", "status": "待退款", "order_no": "LC1", "customer": "王", "row": 5,
         "actions": [], "missing": ["缺退款銀行代碼／帳號（P／Q）"], "manual": []}]


def _at(h, m=0, d=19):
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


@pytest.mark.parametrize("now,expected", [
    (_at(9, 50), _at(11)), (_at(10, 5), _at(11)), (_at(11, 1), None), (_at(14, 2), None),
])
def test_next_slot_on_workday(now, expected):
    assert chk.next_remind_slot(now, is_workday=lambda d: True) == expected


def test_no_slot_on_holiday():
    assert chk.next_remind_slot(_at(10), is_workday=lambda d: False) is None


def test_todo_only_lists_rows_missing_data():
    rows = [dict(TODO[0]),
            {**TODO[0], "order_no": "LC2", "actions": ["sync"], "missing": []},
            {**TODO[0], "order_no": "LC3", "actions": ["allowance"], "missing": []},
            {**TODO[0], "order_no": "LC4", "missing": [], "manual": ["請到【鯨躍發票】開立發票逐筆開立"]}]
    assert [item["order_no"] for item in chk.todo_items(rows)] == ["LC1"]


def test_notify_creates_event_on_jenny_calendar(monkeypatch):
    calls = []
    monkeypatch.setattr(chk, "next_remind_slot", lambda now: _at(11))
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", lambda **kw: calls.append(kw) or {"id": "e"})
    assert chk._notify(TODO, now=_at(10), log=lambda *_: None) == {"id": "e"}
    kw = calls[0]
    assert kw["calendar_id"] == "jenny@lemonclean.com.tw" and kw["attendee"] == ""
    assert kw["start"] == _at(11) and kw["reminder_minutes"] == [30]
    assert "LC1" in kw["description"] and "缺退款銀行代碼／帳號（P／Q）" in kw["description"]


def test_notify_failure_does_not_use_shared_calendar(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        raise PermissionError("403")

    monkeypatch.setattr(chk, "next_remind_slot", lambda now: _at(11))
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", fake)
    assert chk._notify(TODO, now=_at(10), log=lambda *_: None) is None
    assert len(calls) == 1 and calls[0]["calendar_id"] == "jenny@lemonclean.com.tw"


def test_notify_skips_without_todo(monkeypatch):
    monkeypatch.setattr(chk.calendar_notify, "create_notify_event", lambda **kw: pytest.fail("不應建立"))
    assert chk._notify([], now=_at(10), log=lambda *_: None) is None


def test_run_never_syncs(monkeypatch):
    from tools.finance_management import change_order_batch as batch

    monkeypatch.setattr(batch, "scan_region", lambda region, fix_formulas, log: [
        {**TODO[0], "region": region}, {**TODO[0], "region": region, "actions": ["sync"], "missing": []}])
    monkeypatch.setattr(batch, "sync_region", lambda *a, **k: pytest.fail("每日檢查不應回寫"))
    monkeypatch.setattr(chk, "log_execution", lambda *a, **k: None)
    monkeypatch.setattr(chk, "_notify", lambda todo, log: None)
    outcome = chk.run(regions=["台北", "高雄"])
    assert len(outcome["todo"]) == 2 and outcome["failed"] == []
