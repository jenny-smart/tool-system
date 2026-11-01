"""【整合執行】月初藍新／鯨躍／現金缺口、固定費用、會計事務所費用確認：盤點與執行規則。"""
import json
from datetime import date

from tools.finance_management import accounting_fee_batch as acc
from tools.finance_management import fixed_expense_batch as fixed
from tools.finance_management import month_start_batch as ms
from tools.local_agent import ACTION_HANDLERS


def _task(action, params, status="completed", task_id="t1"):
    return {"task_id": task_id, "action": action, "params_json": json.dumps(params, ensure_ascii=False),
            "status": status, "created_at": "2026/11/01 00:30:00", "finished_at": "2026/11/01 09:00:00"}


# ── 月初 ────────────────────────────────────────────

def test_previous_period_and_params():
    assert ms.previous_period(date(2026, 11, 1)) == "202610"
    assert ms.previous_period(date(2027, 1, 1)) == "202612"
    assert ms.step_params("newebpay_download", "202602")["end_date"] == "2026-02-28"
    assert ms.step_params("newebpay_invoice", "202610")["months"] == "202610"


def test_scan_defaults_nothing_done():
    items = ms.build_items("202610", [], [["地區", "月份"]], [])
    assert [i["step"] for i in items] == list(ms.STEP_ORDER)
    assert all(i["default"] and i["selectable"] for i in items)


def test_scan_queued_batch_is_not_requeued_and_completed_unchecked():
    tasks = [_task(ms.BATCH_ACTION, {"period": "202610", "steps": ["newebpay_download", "cetustek_download"]},
                   status="pending"),
             _task("newebpay.invoice_amounts", {"months": "202610", "area": "全區"}, task_id="t2")]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026/11/01", "1234"]], [])}
    assert not items["newebpay_download"]["selectable"]
    assert not items["cetustek_download"]["selectable"]
    assert items["newebpay_invoice"]["selectable"] and not items["newebpay_invoice"]["default"]
    # 現金缺口還沒算到 10/31 → 預設打勾
    assert items["cash_gap"]["default"]


def test_cash_gap_already_computed_with_invoice_unchecked():
    items = {i["step"]: i for i in ms.build_items(
        "202610", [], [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026/11/01", "1234"]], [["", "台北"], ["日期", "2026/10/31"]])}
    assert not items["cash_gap"]["default"] and items["cash_gap"]["selectable"]


def test_other_period_task_not_matched():
    tasks = [_task("cetustek.download", {"month": "202609", "area": "全區"})]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, [], [])}
    assert items["cetustek_download"]["status"] == "尚未執行"


def test_run_plan_order_and_dependency():
    from tools.newebpay.month_close import INVOICE_FAILED

    calls = []

    def run_step(step, period, _cdp):
        calls.append(step)
        return INVOICE_FAILED if step == ms.COMBINED_NEWEBPAY else 0

    code = ms.run_plan("202610", ["cash_gap", "cetustek_download", "newebpay_invoice", "newebpay_download"],
                       log=lambda _t: None, run_step=run_step, record=False)
    # 藍新收退款＋發票金額一起做（每區登入一次）；發票失敗 → 現金缺口略過
    assert calls == [ms.COMBINED_NEWEBPAY, "cetustek_download"]
    assert code == 1


def test_run_plan_single_newebpay_steps_run_alone():
    calls = []
    ms.run_plan("202610", ["newebpay_invoice", "cash_gap"], log=lambda _t: None,
                run_step=lambda step, *_a: calls.append(step) or 0, record=False)
    assert calls == ["newebpay_invoice", "cash_gap"]


def test_month_close_exit_code():
    from tools.newebpay.month_close import exit_code

    assert exit_code([], []) == 0 and exit_code(["台北"], []) == 1
    assert exit_code([], ["台北"]) == 2 and exit_code(["a"], ["b"]) == 3


def test_queue_and_agent_command():
    created = []
    task = ms.queue_steps("202610", ["cash_gap", "newebpay_download"],
                          lambda action, params, created_by: created.append((action, params)) or {"task_id": "x"},
                          log=lambda _t: None)
    assert task == {"task_id": "x"}
    action, params = created[0]
    assert params["steps"] == ["newebpay_download", "cash_gap"]
    command = ACTION_HANDLERS[action](params)
    assert "tools.finance_management.month_start_batch" in command and "202610" in command


# ── 固定費用 ────────────────────────────────────────

def test_fixed_expense_odd_month():
    items = fixed.mark_status(fixed.planned_items("202611"), {"台北": {"2026.11-辦公室租金"}, "台中": set()})
    by_memo = {i["memo"]: i for i in items}
    assert by_memo["2026.11-辦公室租金"]["status"] == "已新增過"
    assert by_memo["2026.11-辦公室管理費"]["default"] and by_memo["2026.11-辦公室管理費"]["amount"] == 9392
    assert by_memo["2026.11-辦公室租金管理費"]["area"] == "台中"
    assert by_memo["2026.12-辦公室租金管理費"]["amount"] == 18000


def test_fixed_expense_even_month_and_year_end():
    items = fixed.mark_status(fixed.planned_items("202612"), {"台北": set(), "台中": set()})
    taichung = [i for i in items if i["area"] == "台中"]
    assert len(taichung) == 1 and not taichung[0]["selectable"]
    january = [i["memo"] for i in fixed.planned_items("202701") if i["area"] == "台中"]
    assert january == ["2027.01-辦公室租金管理費", "2027.02-辦公室租金管理費"]


# ── 會計事務所 ──────────────────────────────────────

def _memos(period):
    return [i["memo"] for i in acc.planned_items(period)]


def test_accounting_november():
    memos = _memos("202611")
    assert "2026.09～10-台北營業稅" in memos and "2026.09～10-台中營業稅" in memos
    assert "2026.11-辦公室租金代扣繳" in memos and "2026.12-辦公室租金代扣繳" in memos
    assert "2026.12-辦公室二代健保" in memos
    assert not any("營所稅" in m or "記帳費" in m for m in memos)


def test_accounting_january_bookkeeping_and_tax_span():
    items = {i["memo"]: i for i in acc.planned_items("202701")}
    assert "2026.11～12-台北營業稅" in items
    assert items["2027.01-台北半年度記帳費"]["amount"] == 18000
    assert items["2027.01-台中半年度記帳費"]["amount"] == 12000
    july = {i["memo"]: i for i in acc.planned_items("202707")}
    assert july["2027.07-台北半年度記帳費"]["amount"] == 21000
    assert july["2027.07-台中半年度記帳費"]["amount"] == 21000


def test_accounting_income_tax_and_audit_defaults():
    assert "2027.05-營所稅" in _memos("202705") and "2027.09-營所稅" in _memos("202709")
    march = acc.apply_defaults(fixed.mark_status(acc.planned_items("202703"), {"台北": set()}))
    by_memo = {i["memo"]: i for i in march}
    assert by_memo["2027-會計師簽證費"]["default"] and by_memo["2027-會計師簽證費"]["amount"] == 60000
    assert not by_memo["2027.01～02-台北營業稅"]["default"]  # 要先填金額
    assert by_memo["2027.03-辦公室租金代扣繳"]["default"]
    may = {i["memo"]: i for i in acc.apply_defaults(fixed.mark_status(acc.planned_items("202705"), {"台北": set()}))}
    assert may["2027-會計師簽證費"]["selectable"] and not may["2027-會計師簽證費"]["default"]


def test_accounting_even_month_and_reminder():
    items = fixed.mark_status(acc.planned_items("202612"), {"台北": set()})
    assert len(items) == 1 and not items[0]["selectable"]
    title, body = acc.build_reminder_text("202611", acc.apply_defaults(
        fixed.mark_status(acc.planned_items("202611"), {"台北": {"2026.11-辦公室二代健保"}})))
    assert "2026.11" in title and "（請填金額）" in body and "已新增過：2026.11-辦公室二代健保" in body
    assert "會計師簽證費" not in body.split("已新增過")[0]


# ── 月初檢查 ────────────────────────────────────────

def test_month_start_check_cloud_logs():
    from tools.finance_management import month_start_check as chk

    logs = {"monthly_prepaid_report": {"status": "success"},
            "monthly_stored_value_settlement": {"status": "failed", "exit_code": 1}}
    problems = chk.cloud_problems(date(2026, 12, 1), read=lambda _d, name: logs.get(name))
    assert [p["label"] for p in problems] == ["儲值金結算", "儲值金預收", "儲值金完整流程"]
    assert problems[0]["status"] == "失敗（exit 1）" and problems[1]["status"] == "沒有執行紀錄"


def test_month_start_check_pending_items_and_text():
    from datetime import datetime

    from tools.finance_management import month_start_check as chk

    tasks = [_task(ms.BATCH_ACTION, {"period": "202611", "steps": list(ms.STEP_ORDER)}, status="pending")]
    items = ms.build_items("202611", tasks, [], [])
    problems = chk.month_start_problems(items)
    assert len(problems) == 4  # 排隊等 Mac 也算未完成
    done = ms.build_items("202611", [_task(ms.BATCH_ACTION, {"period": "202611", "steps": list(ms.STEP_ORDER)})],
                          [["地區", "月份", "發票日期", "發票金額"], ["台北", "202611", "2026/12/01", "1234"]], [["", "台北"], ["日期", "2026/11/30"]])
    assert chk.month_start_problems(done) == []
    fixed_items = fixed.mark_status(fixed.planned_items("202612"), {"台北": {"2026.12-辦公室租金"}, "台中": set()})
    assert [p["label"] for p in chk.fixed_expense_problems(fixed_items)] == ["固定費用 台北 2026.12-辦公室管理費"]
    title, body = chk.build_reminder(date(2026, 12, 1), datetime(2026, 12, 1, 3, 0), problems)
    assert "4 項" in title and chk.MONTH_START_FIX in body and body.count(chk.MONTH_START_FIX) == 1


def test_invoice_without_amount_is_not_ready():
    # 1 日 08:00 前查會寫入「查無發票」（金額空白）：不算有資料，發票金額步驟要再做
    empty = [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026/11/01", ""]]
    assert ms.invoice_amount_areas(empty, "202610") == []
    tasks = [_task("newebpay.invoice_amounts", {"months": "202610", "area": "全區"})]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, empty, [])}
    assert items["newebpay_invoice"]["default"] and "還沒有發票金額" in items["newebpay_invoice"]["status"]


def test_month_start_check_skips_mac_items_before_queue_hour(monkeypatch):
    from tools.finance_management import fixed_expense_batch, month_start_batch
    from tools.finance_management import month_start_check as chk

    monkeypatch.setattr(chk, "cloud_entries", lambda _day: [])
    monkeypatch.setattr(chk, "statement_entries", lambda _day: [])
    monkeypatch.setattr(fixed_expense_batch, "scan", lambda *_a, **_k: [])
    monkeypatch.setattr(month_start_batch, "scan", lambda period, log=print: ms.build_items(period, [], [], []))
    assert chk.collect(date(2026, 12, 1), log=lambda _t: None, hour=3) == []
    assert len(chk.collect(date(2026, 12, 1), log=lambda _t: None, hour=10)) == 4


def test_month_start_check_run_selected(monkeypatch):
    from tools.finance_management import fixed_expense_batch, month_start_check as chk

    entries = chk.cloud_entries(date(2026, 12, 1), read=lambda _d, name: None)
    entries += chk.fixed_expense_entries(fixed.mark_status(fixed.planned_items("202612"), {"台北": set(), "台中": set()}))
    entries += chk.month_start_entries(ms.build_items("202611", [], [], []))
    selected, notes = chk.plan_run(entries)
    assert "finance_vip_full_pipeline" not in {e.get("key") for e in selected} and notes  # 要等結算／預收

    dispatched, submitted, queued = [], [], []
    monkeypatch.setattr(fixed_expense_batch, "submit", lambda items, **_k: submitted.extend(items) or {"台北": len(items)})
    messages = chk.run_selected(entries, date(2026, 12, 1),
                                lambda action, params, created_by: queued.append(params) or {"task_id": "q"},
                                log=lambda _t: None, dispatch=lambda wf, inputs: dispatched.append((wf, inputs)))
    assert ("scheduled_monthly.yml", {"target": "stored_value_settlement", "period": "202611-2"}) in dispatched
    assert len(dispatched) == 3 and len(submitted) == 2
    assert queued[0]["period"] == "202611" and queued[0]["steps"] == list(ms.STEP_ORDER)
    assert not any(m.startswith("❌") for m in messages)


def test_month_start_check_vip_alone_dispatches_with_date():
    from tools.finance_management import month_start_check as chk

    assert chk.dispatch_inputs("finance_vip_full_pipeline", date(2026, 11, 1)) == \
        ("scheduled_finance.yml", {"target": "vip_full_pipeline", "date": "2026-11-01"})


def test_statement_entries_remind_only_taipei_taichung():
    from tools.finance_management import month_start_check as chk

    def read(area, report):
        last = "2026/10/30" if area in ("台北", "桃園") else "2026/10/20"
        return [["", "帳務日"], ["", last], ["", "2026/10/01"]]

    entries = {e["label"]: e for e in chk.statement_entries(date(2026, 11, 1), read_values=read,
                                                             target=date(2026, 10, 30))}
    assert entries["台北 富邦明細"]["ok"] and entries["桃園 元大明細"]["ok"]
    assert not entries["台中 富邦明細"]["ok"] and entries["台中 富邦明細"]["runnable"]
    assert not entries["高雄 元大明細"]["ok"] and not entries["高雄 元大明細"]["runnable"]
    problems = chk._problems(list(entries.values()))
    assert {p["label"] for p in problems} == {"台中 富邦明細", "台中 元大明細"}  # 新竹／高雄不提醒


def test_statement_follow_up_queues_agent_download():
    from tools.finance_management import month_start_check as chk

    entry = {"label": "台中 富邦明細", "status": "", "ok": False, "kind": "statement",
             "key": ("台中", "fubon.download"), "remind": True, "runnable": True, "fix": ""}
    queued = []
    chk.run_selected([entry], date(2026, 11, 1), lambda action, params, created_by: queued.append((action, params)) or {"task_id": "t"},
                     log=lambda _t: None, dispatch=lambda *_a: None)
    assert queued[0][0] == "fubon.download" and queued[0][1]["start_from"] == "sheet" and queued[0][1]["area"] == "台中"


def test_last_workday_before_month_start():
    from tools.finance_management import month_start_check as chk

    assert chk.last_workday_before(date(2026, 11, 1)) == date(2026, 10, 30)  # 10/31 是週六
