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
    items = {i["step"]: i for i in ms.build_items("202610", tasks, [["地區", "月份"], ["台北", "202610"]], [])}
    assert not items["newebpay_download"]["selectable"]
    assert not items["cetustek_download"]["selectable"]
    assert items["newebpay_invoice"]["selectable"] and not items["newebpay_invoice"]["default"]
    # 現金缺口還沒算到 10/31 → 預設打勾
    assert items["cash_gap"]["default"]


def test_cash_gap_already_computed_with_invoice_unchecked():
    items = {i["step"]: i for i in ms.build_items(
        "202610", [], [["地區", "月份"], ["台北", "202610"]], [["", "台北"], ["日期", "2026/10/31"]])}
    assert not items["cash_gap"]["default"] and items["cash_gap"]["selectable"]


def test_other_period_task_not_matched():
    tasks = [_task("cetustek.download", {"month": "202609", "area": "全區"})]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, [], [])}
    assert items["cetustek_download"]["status"] == "尚未執行"


def test_run_plan_order_and_dependency():
    calls = []

    def run_step(step, period, _cdp):
        calls.append(step)
        return 1 if step == "newebpay_invoice" else 0

    code = ms.run_plan("202610", ["cash_gap", "cetustek_download", "newebpay_invoice", "newebpay_download"],
                       log=lambda _t: None, run_step=run_step, record=False)
    assert calls == ["newebpay_download", "newebpay_invoice", "cetustek_download"]  # 現金缺口被略過
    assert code == 1


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
                          [["地區", "月份"], ["台北", "202611"]], [["", "台北"], ["日期", "2026/11/30"]])
    assert chk.month_start_problems(done) == []
    fixed_items = fixed.mark_status(fixed.planned_items("202612"), {"台北": {"2026.12-辦公室租金"}, "台中": set()})
    assert [p["label"] for p in chk.fixed_expense_problems(fixed_items)] == ["固定費用 台北 2026.12-辦公室管理費"]
    title, body = chk.build_reminder(date(2026, 12, 1), datetime(2026, 12, 1, 3, 0), problems)
    assert "4 項" in title and chk.MONTH_START_FIX in body and body.count(chk.MONTH_START_FIX) == 1
