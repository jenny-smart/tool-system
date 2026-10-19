"""內部請款整合執行與富邦待辦通知：盤點、排任務、留紀錄、提醒內容。"""
from datetime import datetime

from tools.finance_management import internal_payment_batch as batch
from tools.scheduled_daily import fubon_pending_notify as notify


def test_supply_months_only_unpaid():
    values = [["h"], ["", "2026/09", *[""] * 15], ["", "2026/10"] + [""] * 14 + ["2026/10/05"],
              ["", "2026.10", *[""] * 15]]
    assert batch._supply_months(values) == ["202609", "202610"]


def test_payment_items_from_real_filter():
    values = [["h"] * 9, ["待付款", "", "", "", "文具", "500", "王", "李", "812-123"], ["已付款", "", "", "", "x", "9", "a", "b", "c"]]
    items = batch._items_for("payment", "台北", values)
    assert [(i["row"], i["payee"], i["amount"]) for i in items] == [(2, "李", "500")]


def test_queue_groups_by_kind_area_month_and_records_last():
    items = [
        {"kind": "supply", "area": "台北", "row": 8, "month": "202610", "payee": "A", "amount": "100"},
        {"kind": "payment", "area": "台中", "row": 3, "month": "", "payee": "B", "amount": "50"},
        {"kind": "payment", "area": "台北", "row": 5, "month": "", "payee": "C", "amount": "60"},
        {"kind": "supply", "area": "台北", "row": 4, "month": "202609", "payee": "D", "amount": "70"},
    ]
    created = []
    batch.queue_actions(items, lambda a, p, created_by="": created.append((a, p)) or {"task_id": "t"},
                        log=lambda *_: None)
    assert [(a, p.get("area"), p.get("selected_rows"), p.get("month")) for a, p in created[:-1]] == [
        ("fubon.payment_request", "台北", [5], None),
        ("fubon.payment_request", "台中", [3], None),
        ("fubon.supply_purchase", "台北", [8], "202610"),
        ("fubon.supply_purchase", "台北", [4], "202609"),
    ]
    assert created[-1][0] == "internal_payment.record" and len(created[-1][1]["items"]) == 4


def test_record_lists_done_and_pending(monkeypatch):
    selected = [{"kind": "payment", "area": "台北", "row": 5, "month": "", "payee": "李", "amount": "500"},
                {"kind": "deposit", "area": "台北", "row": 9, "month": "", "payee": "", "amount": "300"}]
    monkeypatch.setattr(batch, "scan", lambda areas, kinds, log: [dict(selected[1])])
    calls = []
    from tools.orders_system import calendar_notify
    monkeypatch.setattr(calendar_notify, "create_notify_event", lambda **kw: calls.append(kw))
    result = batch.record(selected, log=lambda *_: None, now=datetime(2026, 10, 20, 15, 0, tzinfo=batch.TW_TZ))
    assert [r["row"] for r in result["done"]] == [5] and [r["row"] for r in result["pending"]] == [9]
    kw = calls[0]
    assert kw["calendar_id"] == "jenny@lemonclean.com.tw" and kw["reminder_minutes"] == [] and kw["attendee"] == ""
    assert kw["summary"] == "本日內部請款已付款：1 筆（未完成 1 筆）"
    assert "台北 請款記錄：1筆，列數：5（李 500）" in kw["description"]


def test_weekly_reminder_has_no_atm_and_uses_lemonclean_calendar(monkeypatch):
    items = [{"kind": "supply", "area": "台中", "row": 4, "memo": "202610 採購（第4列）"}]
    monkeypatch.setattr(notify.batch, "scan", lambda: items)
    calls = []
    monkeypatch.setattr(notify.calendar_notify, "create_notify_event", lambda **kw: calls.append(kw))
    notify.main()
    kw = calls[0]
    assert kw["calendar_id"] == "jenny@lemonclean.com.tw" and kw["reminder_minutes"] == [20, 10]
    assert "ATM" not in kw["summary"] + kw["description"]
    assert "清潔用品採購：1 筆" in kw["description"]


def test_weekly_reminder_skips_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(notify.batch, "scan", lambda: [])
    monkeypatch.setattr(notify.calendar_notify, "create_notify_event", lambda **kw: (_ for _ in ()).throw(AssertionError))
    notify.main()
