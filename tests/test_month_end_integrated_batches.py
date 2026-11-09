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
    assert ms.period_range("202602")[1] == date(2026, 2, 28)
    assert ms.step_params("newebpay", "202610")["period"] == "202610"
    assert ms.normalize_steps(["cash_gap", "newebpay_invoice", "newebpay_download"]) == ["newebpay", "cash_gap"]


def test_scan_defaults_nothing_done():
    items = ms.build_items("202610", [], [["地區", "月份"]], [])
    assert [i["step"] for i in items] == list(ms.STEP_ORDER)
    assert all(i["default"] and i["selectable"] for i in items)


def test_scan_queued_batch_is_not_requeued_and_completed_unchecked():
    tasks = [_task(ms.BATCH_ACTION, {"period": "202610", "steps": ["newebpay", "cetustek_download"]},
                   status="pending"),
             _task("newebpay.invoice_amounts", {"months": "202610", "area": "全區"}, task_id="t2")]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026/11/01", "1234"]], [])}
    assert not items["newebpay"]["selectable"]  # 已排入，不能再排
    assert not items["cetustek_download"]["selectable"]
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


def test_run_plan_cash_gap_runs_even_if_newebpay_fails():
    from tools.newebpay.month_close import INVOICE_FAILED

    calls, lines = [], []

    def run_step(step, period, _cdp):
        calls.append(step)
        return INVOICE_FAILED if step == "newebpay" else 0

    code = ms.run_plan("202610", ["cash_gap", "cetustek_download", "newebpay"],
                       log=lines.append, run_step=run_step, record=False)
    assert calls == ["newebpay", "cetustek_download", "cash_gap"]  # 現金缺口照算
    assert code == 1
    result_line = [l for l in lines if l.startswith(ms.STEP_RESULTS_PREFIX)][0]
    assert '"cash_gap": "成功"' in result_line and "失敗（發票金額）" in result_line


def test_status_per_step_from_result_json():
    task = _task(ms.BATCH_ACTION, {"period": "202610", "steps": list(ms.STEP_ORDER)}, status="failed")
    task["result_json"] = json.dumps({"steps": {"newebpay": "失敗（收退款）", "cetustek_download": "成功", "cash_gap": "成功"}},
                                     ensure_ascii=False)
    items = {i["step"]: i for i in ms.build_items("202610", [task], [["地區", "月份", "日期", "金額"], ["台北", "202610", "", "1"]],
                                                     [["", "台北"], ["日期", "2026/10/31"]])}
    assert items["newebpay"]["default"] and "失敗（收退款）" in items["newebpay"]["status"]
    assert not items["cetustek_download"]["default"] and items["cetustek_download"]["status"].startswith("已完成")
    assert not items["cash_gap"]["default"]


def test_run_plan_legacy_steps_merge_into_one_login():
    calls = []
    ms.run_plan("202610", ["newebpay_invoice", "newebpay_download", "cash_gap"], log=lambda _t: None,
                run_step=lambda step, *_a: calls.append(step) or 0, record=False)
    assert calls == ["newebpay", "cash_gap"]


def test_newebpay_failure_text():
    assert ms._failure_text("newebpay", 2) == "失敗（發票金額）"
    assert ms._failure_text("newebpay", 3) == "失敗（收退款、發票金額）"


def test_month_close_exit_code():
    from tools.newebpay.month_close import exit_code

    assert exit_code([], []) == 0 and exit_code(["台北"], []) == 1
    assert exit_code([], ["台北"]) == 2 and exit_code(["a"], ["b"]) == 3


def test_queue_and_agent_command():
    created = []
    task = ms.queue_steps("202610", ["cash_gap", "newebpay"],
                          lambda action, params, created_by: created.append((action, params)) or {"task_id": "x"},
                          log=lambda _t: None)
    assert task == {"task_id": "x"}
    action, params = created[0]
    assert params["steps"] == ["newebpay", "cash_gap"]
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
    assert january == ["2027.01-辦公室租金管理費", "2027.02-辦公室租金管理費", "2026.11-電費", "2026.12-電費"]


def test_taichung_electricity_on_odd_month():
    """2026-11-05 Jenny：台中單月加前月＋前前月電費；金額輸入合計後除以 2 拆兩筆，沒填不新增。"""
    items = fixed.mark_status(fixed.planned_items("202611"), {"台北": set(), "台中": {"2026.09-電費"}})
    power = {i["memo"]: i for i in items if i.get("electricity")}
    assert power["2026.09-電費"]["status"] == "已新增過"
    assert power["2026.10-電費"]["status"] == "待輸入電費" and not power["2026.10-電費"]["default"]
    filled = {i["memo"]: i for i in fixed.split_electricity(items, 2001) if i.get("electricity")}
    assert filled["2026.10-電費"]["amount"] == 2001 and filled["2026.10-電費"]["default"]
    both = fixed.mark_status(fixed.planned_items("202611"), {"台北": set(), "台中": set()})
    filled = [i["amount"] for i in fixed.split_electricity(both, 87) if i.get("electricity")]
    assert filled == [43, 44]


# ── 會計事務所 ──────────────────────────────────────

def _memos(period):
    return [i["memo"] for i in acc.planned_items(period)]


def test_accounting_november():
    memos = _memos("202611")
    assert "2026.09～10-台北營業稅" in memos and "2026.09～10-台中營業稅" in memos
    assert "2026.11-辦公室租金代扣繳" in memos and "2026.12-辦公室租金代扣繳" in memos
    assert "2026.12-辦公室二代健保" in memos
    assert not any("營所稅" in m for m in memos)
    # 2026-11-09：記帳費每個單月都列出，1、7 月以外預設不勾（參考金額用最近一次 7 月）
    items = {i["memo"]: i for i in acc.apply_defaults(fixed.mark_status(acc.planned_items("202611"), {"台北": set()}))}
    assert items["2026.11-台北半年度記帳費"]["amount"] == 21000 and not items["2026.11-台北半年度記帳費"]["default"]
    assert items["2026.11-台中半年度記帳費"]["amount"] == 21000 and not items["2026.11-台中半年度記帳費"]["default"]


def test_accounting_request_rows_after_transfer():
    items = [{"memo": "2026.09～10-台北營業稅", "category": "營業稅", "amount": 352626},
             {"memo": "2026.09～10-台中營業稅", "category": "營業稅", "amount": 29185},
             {"memo": "2026.11-台中半年度記帳費", "category": "記帳費", "amount": 21000}]
    rows = acc.request_rows(items, "2026/11/09", "2026/11/09 16:30:00")
    assert [r[4] for r in rows["台北"]] == [i["memo"] for i in items]
    assert all(r[0] == "已付款" and r[1] == "2026/11/09" and r[7] == "勤信" and r[8] == "013-0000240035015567"
               for r in rows["台北"])
    assert [r[4] for r in rows["台中"]] == ["2026.09～10-台中營業稅", "2026.11-台中半年度記帳費"]
    assert all(r[0] == "待付款" and r[1] == "" and r[7] == "台北" and r[8] == "" for r in rows["台中"])
    assert "台中" not in acc.request_rows(items[:1], "2026/11/09", "x")


def test_accounting_queue_transfer_merges_into_one_task():
    calls = []
    task = acc.queue_transfer("202611", [{"memo": "a", "category": "營業稅", "amount": 100},
                                         {"memo": "b", "category": "辦公室租金", "amount": 8800}],
                              lambda action, params, created_by="": calls.append((action, params)) or {"task_id": "T1"},
                              log=lambda _t: None)
    assert task == {"task_id": "T1"} and len(calls) == 1
    action, params = calls[0]
    assert action == "fubon.accounting_fee_transfer" and params["area"] == "台北"
    assert sum(i["amount"] for i in params["items"]) == 8900


def test_accounting_transfer_agent_command():
    from tools import local_agent

    command = local_agent.build_fubon_accounting_fee_transfer(
        {"area": "台北", "period": "202611", "items": [{"memo": "a", "amount": 100}]})
    assert "tools.bank_statement.fubon_accounting_fee_transfer" in command and "202611" in command


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


def test_invoice_without_amount_is_not_ready():
    # 1 日 08:00 前查會寫入「查無發票」（金額空白）：不算有資料，發票金額步驟要再做
    empty = [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026/11/01", ""]]
    assert ms.invoice_amount_areas(empty, "202610") == []
    tasks = [_task(ms.BATCH_ACTION, {"period": "202610", "steps": ["newebpay"]})]
    items = {i["step"]: i for i in ms.build_items("202610", tasks, empty, [])}
    assert items["newebpay"]["default"] and "還沒有發票金額" in items["newebpay"]["status"]


def test_cetustek_step_skips_kaohsiung_and_makes_taichung_pdf():
    params = ms.step_params("cetustek_download", "202611")
    command = ACTION_HANDLERS["cetustek.download"](params)
    assert command[command.index("--skip-area") + 1] == "高雄"
    assert command[command.index("--paper-pdf-area") + 1] == "台中"
    assert command[command.index("--copy-area") + 1] == "新竹=高雄"
    assert "202611" in command


def test_update_invoice_sheets_paper_and_odd_month_prize(monkeypatch):
    from tools.invoice_center import invoice_archive

    calls = []

    class FakeProcessor:
        configs = {"台北": 1, "新竹": 1, "高雄": 1}

        def update_paper(self, *, area, yyyymm):
            calls.append(("paper", area, yyyymm)); return 1

        def update_prize_period(self, *, area, period8):
            calls.append(("prize", area, period8)); return 2

        def write_update_log(self, **_k):
            pass

    monkeypatch.setattr(invoice_archive, "InvoiceArchiveProcessor", FakeProcessor)
    assert ms.update_invoice_sheets("202611", log=lambda _t: None) == 0
    assert ("prize", "台北", "20260910") in calls and ("paper", "高雄", "202611") in calls  # 高雄是新竹的複本，一樣更新
    calls.clear()
    ms.update_invoice_sheets("202610", log=lambda _t: None)
    assert [c[0] for c in calls] == ["paper", "paper", "paper"]  # 雙月沒有中獎發票


def test_copy_kaohsiung_from_hsinchu(tmp_path):
    from tools.invoice_center.ei_export_all import copy_as_area
    from tools.newebpay.month_close import copy_for_area

    src = tmp_path / "新竹" / "202610紙本發票-新竹.zip"
    src.parent.mkdir()
    src.write_bytes(b"x")
    copied = copy_as_area(src, "新竹", "高雄")
    assert copied == tmp_path / "高雄" / "202610紙本發票-高雄.zip" and copied.read_bytes() == b"x"
    assert copy_as_area(None, "新竹", "高雄") is None
    csv = tmp_path / "新竹" / "202610-2藍新收款-新竹.csv"
    csv.write_text("a")
    assert copy_for_area(csv, "新竹", "高雄").name == "202610-2藍新收款-高雄.csv"


def test_newebpay_invoice_query_range_starts_previous_month():
    from tools.newebpay.invoice_amounts import invoice_date_for, query_range

    assert query_range([invoice_date_for("202610")]) == ("2026-10-01", "2026-11-01")
    assert query_range([invoice_date_for("202612")]) == ("2026-12-01", "2027-01-01")


def test_pick_fee_invoice_allows_late_issue():
    from tools.newebpay.invoice_amounts import pick_fee_invoice

    rows = [{"invoice_date": "2026-10-01", "amount": "1"}, {"invoice_date": "2026-11-03", "amount": "3"},
            {"invoice_date": "2026-11-02", "amount": "2"}, {"invoice_date": "2026-11-20", "amount": "9"}]
    assert pick_fee_invoice(rows, "2026-11-01")["amount"] == "2"  # 晚一天開也抓得到，取最早
    assert pick_fee_invoice(rows + [{"invoice_date": "2026-11-01", "amount": "0"}], "2026-11-01")["amount"] == "0"
    assert pick_fee_invoice(rows[:1], "2026-11-01") == {}
    assert pick_fee_invoice([{"invoice_date": "2026-10-31", "amount": "5"}], "2026-11-01")["amount"] == "5"


def test_newebpay_step_default_when_balance_missing():
    invoice = [["地區", "月份", "發票日期", "發票金額"], ["台北", "202610", "2026-11-01", "1234"]]
    done = {i["step"]: i for i in ms.build_items("202610", [], invoice, [])}
    assert not done["newebpay"]["default"]
    missing = {i["step"]: i for i in ms.build_items("202610", [], invoice, [], balance_done=False)}
    assert missing["newebpay"]["default"] and "帳務" in missing["newebpay"]["status"]
    assert not ms._balance_done([["", "台北", "台中", "桃園", "新竹", "高雄"]], "202610")
