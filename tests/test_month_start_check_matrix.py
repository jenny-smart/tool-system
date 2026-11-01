"""【整合執行】月初（1日所有排程）：依功能盤點各地區狀態、完成標記、只補做沒完成的地區。"""
from datetime import date

from tools.finance_management import month_start_batch as ms
from tools.finance_management import month_start_check as chk

DAY = date(2026, 11, 1)  # 期別 202610
MONTHLY_LOG = [
    ["執行時間", "系統", "功能", "執行方式", "區域", "期別/日期", "目標位置", "來源檔名", "結果", "訊息"],
    ["2026-10-31 18:02:56", "", "已退款", "排程", "all", "202610-2", "", "", "成功", ""],
    ["2026-11-01 0:02:15", "", "預收", "排程", "台北", "202610-2", "", "", "成功", ""],
    ["2026-11-01 0:02:22", "", "預收", "排程", "台中", "202610-2", "", "", "失敗", "timeout"],
    ["2026-11-01 0:32:52", "", "儲值金結算", "排程", "all", "202610-2", "", "", "成功", ""],
    ["2026-11-01 0:43:09", "", "儲值金預收", "排程", "all", "202610-2", "", "", "成功", ""],
]
VIP_LOG = [["項目", "202610"]] + [[f"{a}儲值金{k}搬運時間", "2026/11/01 1:05" if a != "高雄" else ""]
                                  for a in chk.AREAS for k in ("結算", "預收")]
EI_LOG = [["執行時間", "功能", "地區", "期別", "狀態", "筆數", "訊息"]] + \
    [["t", "紙本發票更新", a, "202610", "成功", "1", ""] for a in chk.AREAS] + \
    [["t", "紙本發票PDF建檔", "台中", "202610", "失敗", "0", "click timeout"]]
INVOICE = [["區域", "月份", "發票日期", "發票金額", "發票號碼", "發票狀態"],
           ["台北", "202610", "", "51,149", "", "開立"], ["台中", "202610", "", "3,504", "", "開立"],
           ["桃園", "202610", "", "", "", "查無發票"], ["新竹", "202610", "", "454", "", "開立"],
           ["高雄", "202610", "", "454", "", "開立"]]
FILES = [f"202610-2藍新{k}-{a}.csv" for a in chk.AREAS for k in ("收款", "退款")]
CASH = [["", "台北", "台中", "桃園", "新竹", "高雄", "ALL"], ["日期"] + ["2026/10/31"] * 6,
        ["富邦餘額", "1", "2", "錯誤：「財報」", "3", "4"]]


def readers(**overrides):
    base = {"月排程執行Log": lambda: MONTHLY_LOG, "儲值金月度作業紀錄": lambda: VIP_LOG,
            "本機Agent任務": lambda: [], "藍新收退款檔": lambda: FILES, "藍新發票總表": lambda: INVOICE,
            "鯨躍發票更新Log": lambda: EI_LOG, "現金缺口試算": lambda: CASH,
            "固定費用": lambda: [{"area": "台北", "memo": "2026.11-辦公室租金", "default": True},
                             {"area": "台中", "memo": "2026.11-辦公室租金管理費", "default": False}],
            "財報": lambda area, report: [["", "帳務日"], ["", "2026/10/30" if area != "台中" else "2026/09/18"]]}
    base.update(overrides)
    return base


def rows_by_key(**overrides):
    return {r["key"]: r for r in chk.collect_rows(DAY, log=lambda _t: None, readers=readers(**overrides))}


def test_rows_per_function_and_area_with_done_mark():
    rows = rows_by_key()
    assert [r["label"] for r in rows.values()][:6] == ["已退款", "預收", "儲值金結算", "儲值金預收",
                                                       "儲值金彙整（完整流程）", "藍新收／退款"]
    assert rows["refund_report"]["done"] and rows["refund_report"]["cells"]["高雄"][0] == chk.OK  # all 成功
    assert not rows["prepaid_report"]["done"] and rows["prepaid_report"]["todo"] == ["台中", "桃園", "新竹", "高雄"]
    assert rows["vip_full_pipeline"]["todo"] == ["高雄"]
    assert rows["newebpay_files"]["done"]
    assert rows["newebpay_invoice"]["done"] and rows["newebpay_invoice"]["cells"]["桃園"][0] == chk.NONE
    assert rows["paper"]["done"] and rows["prize"]["done"] and rows["prize"]["cells"]["台北"][0] == chk.NA
    assert rows["pdf"]["todo"] == ["台中"] and rows["pdf"]["cells"]["台北"][0] == chk.NA
    assert rows["cash_gap"]["todo"] == ["桃園"]
    assert rows["fixed"]["todo"] == ["台北"] and rows["fixed"]["items"][0]["memo"] == "2026.11-辦公室租金"
    assert rows["yuanta"]["todo"] == ["台中"]  # 只看台北／台中


def test_odd_period_has_prize_row():
    rows = {r["key"]: r for r in chk.collect_rows(date(2026, 12, 1), log=lambda _t: None, readers=readers())}
    assert rows["prize"]["label"] == "發票中獎（09-10）" and rows["prize"]["prize_period"] == "20260910"
    assert not rows["prize"]["done"]


def test_waiting_agent_task_not_runnable():
    task = {"task_id": "t", "action": ms.BATCH_ACTION, "params_json": '{"period": "202610", "steps": ["newebpay"]}',
            "status": "running"}
    rows = rows_by_key(**{"本機Agent任務": lambda: [task], "藍新收退款檔": lambda: []})
    assert rows["newebpay_files"]["cells"]["台北"][0] == chk.WAIT and not rows["newebpay_files"]["runnable"]


def test_run_rows_only_unfinished_areas_in_order():
    rows = list(rows_by_key(**{"藍新發票總表": lambda: INVOICE[:2]}).values())
    order, queued = [], []
    runners = {"monthly": lambda target, p2, area, log: order.append((target, area)),
               "vip": lambda day, log: order.append("vip") or 0,
               "fixed": lambda items, **_k: order.append("fixed") or {"台北": len(items)},
               "invoice_update": lambda kind, area, period: order.append((kind, area)) or 1}
    messages = chk.run_rows(rows, DAY, lambda action, params, created_by: queued.append((action, params)) or {"task_id": "q"},
                            log=lambda _t: None, runners=runners)
    assert order[:4] == [("prepaid_report", "台中"), ("prepaid_report", "桃園"), ("prepaid_report", "新竹"),
                         ("prepaid_report", "高雄")]
    assert order[4:] == ["vip", "fixed"]
    actions = [a for a, _p in queued]
    assert actions == ["fubon.download", "yuanta.download", "cetustek.paper_invoice_pdf", ms.BATCH_ACTION]
    assert queued[-1][1]["steps"] == ["newebpay", "cash_gap"]  # 現金缺口跟在藍新後面
    assert not any(m.startswith("❌") for m in messages)


def test_problems_and_reminder():
    rows = list(rows_by_key().values())
    early = [r["key"] for r in chk.problems(rows, hour=3)]
    assert "pdf" not in early and "prepaid_report" in early
    title, body = chk.build_reminder(DAY, __import__("datetime").datetime(2026, 11, 1, 10), chk.problems(rows))
    assert "預收：未完成：台中❌" in body and title.startswith(chk.REMINDER_PREFIX)


def test_auto_fix_fixed_and_vip(tmp_path, monkeypatch):
    monkeypatch.setattr(chk, "LOG_ROOT", tmp_path)
    rows = list(rows_by_key().values())
    submitted, runs = [], []
    done = chk.auto_fix(DAY, rows, log=lambda _t: None, run_vip=lambda d: runs.append(d) or 1,
                        submit_fixed=lambda items, **_k: submitted.extend(items) or {"台北": len(items)})
    assert [i["memo"] for i in submitted] == ["2026.11-辦公室租金"] and runs == [DAY]
    assert "儲值金彙整：失敗" in done
    for _ in range(2):
        chk.auto_fix(DAY, rows, log=lambda _t: None, run_vip=lambda d: runs.append(d) or 1, submit_fixed=lambda *a, **k: {})
    assert len(runs) == 2  # 最多自動試 2 次


def test_vip_auto_fix_waits_for_sources():
    log = MONTHLY_LOG[:4]  # 沒有儲值金結算／預收紀錄
    rows = list(rows_by_key(**{"月排程執行Log": lambda: log}).values())
    runs = []
    chk.auto_fix(DAY, rows, log=lambda _t: None, run_vip=lambda d: runs.append(d) or 0, submit_fixed=lambda *a, **k: {})
    assert runs == []
