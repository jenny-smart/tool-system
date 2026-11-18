"""內部請款整合執行與富邦待辦通知：盤點、排任務、留紀錄、提醒內容。"""
from datetime import datetime

from tools.finance_management import internal_payment_batch as batch
from tools.scheduled_daily import fubon_pending_notify as notify


def test_supply_months_only_unpaid():
    # Q 欄（index 16）有匯款日期＝已付款；只有 S 欄（index 18）請款日期的列還沒付款
    values = [["h"], ["", "2026/09", *[""] * 17], ["", "2026/10"] + [""] * 14 + ["2026/10/05"],
              ["", "2026.10", *[""] * 17], ["", "2026.02"] + [""] * 14 + ["2026/02/10"],
              ["", "2026.11"] + [""] * 16 + ["2026/11/18"]]
    assert batch._supply_months(values) == ["202609", "202610", "202611"]


def test_payment_items_from_real_filter():
    values = [["h"] * 9, ["待付款", "", "", "", "文具", "500", "王", "李", "812-123"], ["已付款", "", "", "", "x", "9", "a", "b", "c"]]
    items = batch._items_for("payment", "台北", values)
    assert [(i["row"], i["payee"], i["amount"]) for i in items] == [(2, "李", "500")]


def test_queue_one_task_per_area_and_records_last():
    items = [
        {"kind": "supply", "area": "台北", "row": 8, "month": "202610", "payee": "A", "amount": "100"},
        {"kind": "payment", "area": "台中", "row": 3, "month": "", "payee": "B", "amount": "50"},
        {"kind": "payment", "area": "台北", "row": 5, "month": "", "payee": "C", "amount": "60"},
        {"kind": "supply", "area": "台北", "row": 4, "month": "202609", "payee": "D", "amount": "70"},
        {"kind": "deposit", "area": "台北", "row": 9, "month": "", "payee": "", "amount": "2000"},
    ]
    created = []
    batch.queue_actions(items, lambda a, p, created_by="": created.append((a, p)) or {"task_id": "t"},
                        log=lambda *_: None)
    assert [a for a, _ in created] == ["fubon.internal_payment"] * 2 + ["internal_payment.record"]
    taipei, taichung = created[0][1], created[1][1]
    assert (taipei["area"], taipei["payment_rows"], taipei["deposit_rows"], taipei["supply"]) == (
        "台北", [5], [9], {"202609": [4], "202610": [8]})
    assert (taichung["area"], taichung["payment_rows"], taichung["deposit_rows"], taichung["supply"]) == (
        "台中", [3], [], {})
    assert len(created[-1][1]["items"]) == 5


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
    assert kw["calendar_id"] == "jenny@hers.com.tw" and kw["reminder_minutes"] == [] and kw["attendee"] == ""
    assert kw["summary"] == "本日內部請款已付款：1 筆（未完成 1 筆）"
    assert "台北 請款記錄：1筆，列數：5（李 500）" in kw["description"]


def test_weekly_reminder_has_no_atm_and_uses_lemonclean_calendar(monkeypatch):
    items = [{"kind": "supply", "area": "台中", "row": 4, "memo": "202610 採購（第4列）"}]
    monkeypatch.setattr(notify.batch, "scan", lambda: items)
    calls = []
    monkeypatch.setattr(notify.calendar_notify, "create_notify_event", lambda **kw: calls.append(kw))
    notify.main()
    kw = calls[0]
    assert kw["calendar_id"] == "jenny@hers.com.tw" and kw["reminder_minutes"] == [20, 10]
    assert "ATM" not in kw["summary"] + kw["description"]
    assert "清潔用品採購：1 筆" in kw["description"]


def test_weekly_reminder_skips_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(notify.batch, "scan", lambda: [])
    monkeypatch.setattr(notify.calendar_notify, "create_notify_event", lambda **kw: (_ for _ in ()).throw(AssertionError))
    notify.main()


def test_merge_by_payee_sums_same_payee_and_account():
    from tools.bank_statement.fubon_payment_request_filter import merge_by_payee, pending_payment_requests

    header = [""] * 9
    row = lambda payee, account, amount: ["待付款", "", "", "", "說明", amount, "請款人", payee, account]  # noqa: E731
    values = [header, row("王小明", "812-123", "100"), row("王小明", "812-123", "250"),
              row("王小明", "812-999", "70"), row("陳大華", "700-1", "30")]
    merged = merge_by_payee(pending_payment_requests(values))
    assert [(m["name"], m["rows"], m["amount"]) for m in merged] == [
        ("王小明", [2, 3], "350"), ("王小明", [4], "70"), ("陳大華", [5], "30"),
    ]
    assert "合併 2 筆" in merged[0]["memo"]


def test_queue_passes_only_rows_marked_merge():
    from tools.finance_management import internal_payment_batch as batch

    created = []
    items = [{"kind": "payment", "area": "台北", "row": 5}, {"kind": "payment", "area": "台北", "row": 6},
             {"kind": "deposit", "area": "台北", "row": 8}, {"kind": "payment", "area": "台中", "row": 5}]
    batch.queue_actions(items, lambda action, params, created_by="": created.append((action, params)) or {},
                        log=lambda *_: None, merge_rows={("台北", 6), ("台北", 8)})
    assert created[0][1]["payment_rows"] == [5, 6]
    assert created[0][1]["merge_rows"] == [6]  # 押金列勾合併不算
    assert "merge_rows" not in created[1][1]  # 台中同列號沒勾合併


def test_merge_preview_groups_marked_rows_by_payee_and_account():
    from tools.finance_management import internal_payment_batch as batch

    items = [{"kind": "payment", "area": "台中", "row": r, "payee": "館前路", "account": "123", "amount": a}
             for r, a in ((46, "18000"), (47, "18000"), (48, "64"), (49, "64"))]
    groups = batch.merge_preview(items, {("台中", 46), ("台中", 47), ("台中", 48)})
    assert groups == [{"area": "台中", "payee": "館前路", "rows": [46, 47, 48], "amount": "36064"}]
    assert batch.merge_preview(items, {("台中", 46)}) == []


def test_prepare_merges_only_marked_rows(monkeypatch):
    from tools.bank_statement import fubon_payment_request as mod

    values = [["A"]] + [["待付款", "", "", "", f"m{i}", amt, "", "館前路", "812-123"]
                        for i, amt in enumerate(("100", "200", "300"))]
    monkeypatch.setattr(mod, "read_report_values", lambda *_: values)
    monkeypatch.setattr(mod, "resolve_report_location", lambda *_: ("sid", "title"))
    monkeypatch.setattr(mod, "get_sheets_service", lambda: None)
    picked = mod.prepare("台中", {2, 3, 4}, {2, 3})["selected"]
    assert [(m["rows"], m["amount"]) for m in picked] == [([2, 3], "300"), ([4], "300")]
    assert len(mod.prepare("台中", {2, 3, 4}, True)["selected"]) == 1
    assert len(mod.prepare("台中", {2, 3, 4})["selected"]) == 3


def test_agent_command_adds_merge_flag():
    from tools.local_agent import build_fubon_payment_request

    base = {"area": "台北", "selected_rows": [2, 3]}
    assert "--merge" not in build_fubon_payment_request(base)
    assert build_fubon_payment_request({**base, "merge": True})[-1] == "--merge"


def test_agent_command_for_area_batch():
    from tools.local_agent import build_fubon_internal_payment

    cmd = build_fubon_internal_payment({"area": "台北", "payment_rows": [91], "deposit_rows": [107, 108],
                                        "supply": {"202610": [4], "202609": []}, "merge_rows": [91]})
    assert cmd[2] == "tools.bank_statement.fubon_internal_payment"
    assert cmd[cmd.index("--payment-rows") + 1] == "91"
    assert cmd[cmd.index("--deposit-rows") + 1] == "107,108"
    assert cmd[cmd.index("--supply") + 1] == '{"202610": [4]}'
    assert cmd[-2:] == ["--merge-rows", "91"]


def _area_run(monkeypatch, fail_prepare=()):
    from contextlib import nullcontext
    from unittest.mock import MagicMock

    from tools.bank_statement import fubon_internal_payment as combined

    events = []
    for name in ("fubon_payment_request", "fubon_deposit_refund", "fubon_supply_purchase"):
        module = getattr(combined, name)

        def prepare(*args, name=name):
            if name in fail_prepare:
                raise ValueError("bad sheet")
            events.append(("prepare", name, args[-1] if name == "fubon_payment_request" else args[1:]))
            return {"selected": [1]}

        monkeypatch.setattr(module, "prepare", prepare)
        monkeypatch.setattr(module, "pay", lambda c, page, *a, name=name: events.append(("pay", name)) or page)
    monkeypatch.setattr(combined, "sync_playwright", lambda: nullcontext())
    monkeypatch.setattr(combined, "connect_existing_chrome", lambda *a: (None, MagicMock()))
    monkeypatch.setattr(combined, "load_account", MagicMock())
    monkeypatch.setattr(combined, "ensure_login", lambda *a: events.append(("login",)) or MagicMock())
    monkeypatch.setattr(combined, "logout_quietly", lambda *a: events.append(("logout",)))
    monkeypatch.setattr(combined, "run_download", lambda *a: events.append(("download",)))
    return combined, events


def test_area_batch_logs_in_once_and_downloads_after_all_transfers(monkeypatch):
    from pathlib import Path

    combined, events = _area_run(monkeypatch)
    assert combined.run("台北", {91}, {107, 108}, {"202610": {4}, "202609": {2}}, Path("."), "cdp", merge=True) == 0
    assert [e for e in events if e[0] != "prepare"] == [
        ("login",), ("pay", "fubon_payment_request"), ("pay", "fubon_deposit_refund"),
        ("pay", "fubon_supply_purchase"), ("pay", "fubon_supply_purchase"), ("download",), ("logout",)]
    assert events[0] == ("prepare", "fubon_payment_request", True)
    assert [e[2] for e in events if e[:2] == ("prepare", "fubon_supply_purchase")] == [
        ("202609", {2}, set(), None), ("202610", {4}, set(), None)]


def test_area_batch_skips_kind_that_fails_before_login(monkeypatch):
    import pytest
    from pathlib import Path

    combined, events = _area_run(monkeypatch, fail_prepare=("fubon_deposit_refund",))
    assert combined.run("台北", {91}, {107}, {}, Path("."), "cdp") == 1
    assert ("pay", "fubon_deposit_refund") not in events and ("download",) in events

    combined, events = _area_run(monkeypatch, fail_prepare=("fubon_payment_request", "fubon_deposit_refund"))
    with pytest.raises(ValueError, match="沒有可轉帳的資料"):
        combined.run("台北", {91}, {107}, {}, Path("."), "cdp")
    assert ("login",) not in events


def test_queue_passes_supply_split_per_month():
    from tools.finance_management import internal_payment_batch as batch

    created = []
    items = [{"kind": "supply", "area": "台北", "row": 4, "month": "202610"},
             {"kind": "supply", "area": "台北", "row": 9, "month": "202610"}]
    batch.queue_actions(items, lambda action, params, created_by="": created.append((action, params)) or {},
                        log=lambda *_: None, split_supply={("台北", "202610", 9)})
    assert created[0][1]["supply_split"] == {"202610": [9]}


def test_agent_commands_pass_merge_and_split_rows():
    from tools.local_agent import build_fubon_atm_refund, build_fubon_internal_payment, build_fubon_supply_purchase

    assert build_fubon_atm_refund({"area": "台北", "selected_rows": [2, 3], "merge_rows": [2, 3]})[-2:] == \
        ["--merge-rows", "2,3"]
    assert "--merge-rows" not in build_fubon_atm_refund({"area": "台北", "selected_rows": [2]})
    assert build_fubon_supply_purchase({"area": "台北", "month": "202610", "selected_rows": [4],
                                        "split_rows": [4]})[-2:] == ["--split-rows", "4"]
    cmd = build_fubon_internal_payment({"area": "台北", "supply": {"202610": [4]}, "supply_split": {"202610": [4]}})
    assert cmd[-2:] == ["--supply-split", '{"202610": [4]}']


def test_supply_same_vendor_is_one_item_and_merge_box_does_not_apply():
    """清潔用品採購：同一廠商（同銀行帳號）彙整成一筆，不同廠商分開；「合併」預設不勾、也不影響清潔用品採購。"""
    from tools.finance_management import internal_payment_batch as batch
    from tools.finance_management import internal_payment_batch_ui as ui

    def row(amount, supplier, bank, account):
        r = [""] * 19
        r[1], r[5], r[10], r[13], r[14] = "2026.11", supplier, amount, bank, account
        return r

    values = [["h"], row("780", "福吉生活百貨", "808(玉山銀行)", "0129940051707"),
              row("1680", "福吉生活百貨", "808(玉山銀行)", "0129940051707"),
              row("4800", "檸檬嚴選", "806(元大銀行)", "20342008886999")]
    items = batch._items_for("supply", "台北", values)
    assert [(i["payee"], i["rows"], i["amount"]) for i in items] == [
        ("福吉生活百貨", [2, 3], "2460"), ("檸檬嚴選", [4], "4800")]
    assert list(ui._rows(items)[ui.MERGE_COLUMN]) == [False, False]

    created = []
    batch.queue_actions(items, lambda action, params, created_by="": created.append((action, params)) or {},
                        log=lambda *_: None)
    params = created[0][1]
    assert params["supply"] == {"202611": [2, 3, 4]}
    assert params["supply_merge"] == {"202611": [2, 3, 4]}  # Agent 依帳號彙整：福吉一筆、檸檬嚴選一筆


def test_queue_sends_supply_merge_rows_and_agent_flag():
    from tools.finance_management import internal_payment_batch as batch
    from tools.local_agent import build_fubon_internal_payment

    created = []
    items = [{"kind": "supply", "area": "台北", "row": r, "rows": [r], "month": "202611"} for r in (148, 149, 154)]
    batch.queue_actions(items, lambda action, params, created_by="": created.append((action, params)) or {},
                        log=lambda *_: None, merge_supply={("台北", "202611", 148), ("台北", "202611", 149)})
    params = created[0][1]
    assert params["supply"] == {"202611": [148, 149, 154]}
    assert params["supply_merge"] == {"202611": [148, 149]}
    cmd = build_fubon_internal_payment(params)
    assert cmd[cmd.index("--supply-merge") + 1] == '{"202611": [148, 149]}'

    created.clear()  # 沒勾合併也要帶 supply_merge（空），Agent 才知道要每列各自轉帳
    batch.queue_actions(items, lambda action, params, created_by="": created.append((action, params)) or {},
                        log=lambda *_: None, merge_supply=set())
    assert created[0][1]["supply_merge"] == {"202611": []}
