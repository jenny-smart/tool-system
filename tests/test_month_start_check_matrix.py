"""【整合執行】月初（1日所有排程）：依功能盤點各地區狀態、完成標記、只補做沒完成的地區。"""
import pytest
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
            "鯨躍發票更新Log": lambda: EI_LOG, "現金缺口試算": lambda: CASH, "月初檢查不提醒": lambda: [],
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
    # 查無發票先算 ❌，要確認過才算完成
    assert rows["newebpay_invoice"]["todo"] == ["桃園"] and chk.unconfirmed_no_invoice_areas(rows["newebpay_invoice"]) == ["桃園"]
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
    # 藍新只有部分地區沒做：只重跑那幾區（不是五區全跑），現金缺口排在後面
    assert actions[:3] == ["fubon.download", "yuanta.download", "cetustek.paper_invoice_pdf"]
    assert set(actions[3:-1]) == {"newebpay.month_close"} and actions[-1] == ms.BATCH_ACTION
    newebpay = {tuple(p["areas"]): p["part"] for a, p in queued if a == "newebpay.month_close"}
    assert all(len(areas) < len(chk.AREAS) for areas in newebpay)  # 只跑沒完成的地區，高雄不查帳務
    assert queued[-1][1]["steps"] == ["cash_gap"]
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


def test_confirm_no_invoice_marks_done():
    writes = []
    row_no = chk.confirm_no_invoice("桃園", "202610", values=INVOICE, write=lambda rng, vals: writes.append((rng, vals)))
    assert row_no == 4 and writes == [("D4", [[0]]), ("F4", [["確認無發票"]])]
    confirmed = [r[:] for r in INVOICE]
    confirmed[3][3], confirmed[3][5] = "0", "確認無發票"
    rows = rows_by_key(**{"藍新發票總表": lambda: confirmed})
    assert rows["newebpay_invoice"]["done"] and rows["newebpay_invoice"]["cells"]["桃園"] == (chk.OK, "確認無發票")


def test_statement_done_when_downloaded_past_month_end_even_without_late_transactions():
    task = {"action": "yuanta.download", "status": "completed",
            "params_json": '{"area": "台中", "end_date": "2026-11-01", "start_from": "sheet"}'}
    rows = rows_by_key(**{"本機Agent任務": lambda: [task]})
    assert rows["yuanta"]["cells"]["台中"][0] == chk.OK and "已下載到 11/01" in rows["yuanta"]["cells"]["台中"][1]
    assert rows["fubon"]["cells"]["台中"][0] == chk.FAIL  # 富邦沒有下載任務、最後一筆 9/18
    old = dict(task, params_json='{"area": "台中", "end_date": "2026-10-25"}')
    assert rows_by_key(**{"本機Agent任務": lambda: [old]})["yuanta"]["cells"]["台中"][0] == chk.FAIL


def test_newebpay_rerun_only_unfinished_area_then_cash_gap():
    invoice = [r[:] for r in INVOICE]
    rows = [r for r in rows_by_key(**{"藍新發票總表": lambda: invoice}).values()
            if r["key"] in ("newebpay_invoice", "cash_gap")]
    queued = []
    chk.run_rows(rows, DAY, lambda action, params, created_by: queued.append((action, params)) or {"task_id": "q"},
                 log=lambda _t: None, runners={})
    assert queued[0] == ("newebpay.month_close", {"period": "202610", "areas": ["桃園"], "part": "invoice",
                                                  "cdp_url": "http://127.0.0.1:9222"})
    assert queued[1][0] == ms.BATCH_ACTION and queued[1][1]["steps"] == ["cash_gap"]  # 藍新做完才算
    from tools.local_agent import ACTION_HANDLERS
    command = ACTION_HANDLERS["newebpay.month_close"](queued[0][1])
    assert command[command.index("--area") + 1] == "桃園" and command[command.index("--part") + 1] == "invoice"


def test_vip_without_source_files_reruns_settlement_and_prepaid():
    rows = [r for r in rows_by_key().values() if r["key"] == "vip_full_pipeline"]
    calls, codes = [], iter([chk.VIP_NO_SOURCE, 0])
    messages = chk.run_rows(rows, DAY, lambda *a, **k: {}, log=lambda _t: None, runners={
        "vip": lambda day, log: calls.append("vip") or next(codes),
        "monthly": lambda target, p2, area, log: calls.append((target, area))})
    assert calls == ["vip", ("stored_value_settlement", "all"), ("stored_value_prepaid", "all"), "vip"]
    assert messages[-1] == "✅ 儲值金彙整（完整流程）：完成"


def test_missing_bank_account_is_not_an_error():
    def read(area, report):
        if area == "高雄" and report == "富邦更新":
            raise RuntimeError("試算表 x 找不到「富邦更新」分頁")
        return [["", "帳務日"], ["", "2026/10/30"]]
    rows = rows_by_key(**{"財報": read})
    assert rows["fubon"]["cells"]["高雄"] == (chk.NA, "沒有這個帳戶")


def test_vip_period_folder_prefers_duplicate_with_source_files():
    from services.vip_workflow import VipStoredValueWorkflow

    folder = "application/vnd.google-apps.folder"

    class Drive:
        def find_folder(self, parent, name):
            return {"id": "year"} if name == "2026儲值金" else None

        def list_children(self, folder_id):
            return {"year": [{"id": "empty", "name": "202610", "mimeType": folder, "modifiedTime": "2026-11-01T02"},
                             {"id": "src", "name": "202610", "mimeType": folder, "modifiedTime": "2026-10-31T16"}],
                    "empty": [{"name": "202610VIP儲值金彙整", "mimeType": "application/vnd.google-apps.spreadsheet"}],
                    "src": [{"name": "202610儲值金結算-台北.xlsx", "mimeType": "x"}]}[folder_id]

        def get_or_create_folder(self, *_a):
            raise AssertionError("不該再建新資料夾")

    workflow = VipStoredValueWorkflow.__new__(VipStoredValueWorkflow)
    workflow.drive, workflow.root_folder_id = Drive(), "root"
    assert workflow.get_period_folder("202610")["id"] == "src"


def _taipei_rows():
    rows = [[""] * 34 for _ in range(16)]
    rows[0][24:27] = ["購買年份", 2025, 2026]
    rows[0][33] = "Total"
    for r in (1, 2, 3, 4, 8, 9, 10, 11, 12, 13):
        rows[r][25] = f"=SUMIFS($X:$X,$V:$V,Z$1) r{r}"
        rows[r][26] = f"=SUMIFS($X:$X,$V:$V,AA$1) r{r}"
    rows[7][25:27] = [2019, "=AA1"]
    rows[15][25:30] = [700786, 1209881, 899022, 678983, 1660970]  # 手打數字，不影響
    return rows


def test_plan_year_column_adds_next_year_only_when_needed():
    from services.vip_workflow import col_letter, plan_year_column

    assert plan_year_column(_taipei_rows(), 2026) is None
    src, dst, last_row, new_year = plan_year_column(_taipei_rows(), 2027)
    assert (col_letter(src), col_letter(dst), last_row, new_year) == ("AA", "AB", 13, 2027)
    rows = _taipei_rows()
    rows[3][27] = "x"
    with pytest.raises(RuntimeError):
        plan_year_column(rows, 2027)


def test_prepare_settlement_sheets_sets_date_and_year():
    from services.vip_workflow import VipStoredValueWorkflow

    calls = []

    class Ws:
        def __init__(self, title, sheet_id):
            self.title, self.id, self.rows = title, sheet_id, _taipei_rows()

        def update(self, range_name, values, value_input_option):
            calls.append((self.title, range_name, values[0][0]))
            if range_name == "AB1":
                self.rows[0][27] = values[0][0]

        def get(self, _rng, value_render_option):
            return self.rows

    sheets = {t: Ws(t, i) for i, t in enumerate(["台北儲值金結算", "桃園儲值金結算", "VIP總表"])}

    class Summary:
        def worksheet(self, title):
            return sheets[title]

        def worksheets(self):
            return list(sheets.values())

        def batch_update(self, body):
            calls.append(("copy", body["requests"][0]["copyPaste"]["destination"]["startColumnIndex"]))

    workflow = VipStoredValueWorkflow.__new__(VipStoredValueWorkflow)
    messages = workflow.prepare_settlement_sheets("202701", Summary())
    assert calls[0] == ("台北儲值金結算", "W1", "2027/1/31")
    assert ("copy", 27) in calls and ("台北儲值金結算", "AB1", 2027) in calls and ("桃園儲值金結算", "AB1", 2027) in calls
    assert len([m for m in messages if "新增 2027" in m]) == 2
    calls.clear()
    workflow.prepare_settlement_sheets("202610", Summary())
    assert calls == [("台北儲值金結算", "W1", "2026/10/31")]


def test_review_block_parts():
    from tools.finance_management import review_monthly as rm

    vip = [[f"r{r}"] + [r] * 6 for r in range(3, 11)]
    cash = {"富邦餘額": {a: 1 for a in chk.AREAS}, "元大餘額": {a: 2 for a in chk.AREAS}}
    only_vip, _ = rm.build_block_updates("現金缺口", date(2026, 10, 31), vip, None, [], "2026/10")
    ranges = [d["range"] for d in only_vip]
    assert any(r.endswith("3:CI10") for r in ranges) and not any(r.endswith("14") for r in ranges)
    only_cash, _ = rm.build_block_updates("現金缺口", date(2026, 10, 31), [], cash, [], "2026/10", include_vip=False)
    ranges = [d["range"] for d in only_cash]
    assert not any("3:" in r for r in ranges) and "'現金缺口'!CD14" in ranges
    assert rm.same_day("2026/10/31", date(2026, 10, 31)) and not rm.same_day("2026/9/30", date(2026, 10, 31))


def test_newebpay_plan_runs_only_checked_part():
    def row(todo):
        return {"todo": todo}

    both = {"newebpay_files": row(["台北", "高雄"]), "newebpay_invoice": row(["台北", "桃園"])}
    assert chk.newebpay_plan(both) == [("download,invoice", ["台北"]), ("invoice", ["桃園"]), ("download", ["新竹"])]
    three = dict(both, newebpay_balance=row(["台北", "台中"]))
    assert chk.newebpay_plan(three) == [("all", ["台北"]), ("balance", ["台中"]), ("invoice", ["桃園"]),
                                        ("download", ["新竹"])]
    command = ACTION_HANDLERS_LATE()["newebpay.month_close"]({"period": "202610", "areas": ["台北"], "part": "download,invoice"})
    assert [command[i + 1] for i, c in enumerate(command) if c == "--part"] == ["download", "invoice"]
    assert chk.newebpay_plan({"newebpay_invoice": row(["桃園"])}) == [("invoice", ["桃園"])]
    assert chk.newebpay_plan({"newebpay_files": row(["桃園"])}) == [("download", ["桃園"])]
    from tools.local_agent import ACTION_HANDLERS

    assert "--part" not in ACTION_HANDLERS["newebpay.month_close"]({"period": "202610", "areas": ["台北"], "part": "all"})


def test_kaohsiung_needs_no_newebpay_invoice():
    cells = chk.newebpay_invoice_cells([["地區", "月份", "", "金額"]], "202610")
    assert cells["高雄"][0] == chk.NA and cells["台北"][0] == chk.FAIL
    from tools.finance_management.cash_gap import newebpay_invoice_amount

    assert newebpay_invoice_amount("高雄", date(2026, 10, 31)) == 0.0


def test_ignored_items_still_shown_but_not_reminded():
    rows = list(rows_by_key(**{"月初檢查不提醒": lambda: [chk.IGNORE_HEADER, ["prepaid_report", "預收", "台中", ""]]}).values())
    row = next(r for r in rows if r["key"] == "prepaid_report")
    assert not row["done"] and row["ignored"] == ["台中"]
    assert row["remind_todo"] == [a for a in row["todo"] if a != "台中"]
    only_taichung = [dict(r, remind_todo=[]) if r["key"] == "prepaid_report" else r for r in rows]
    assert "prepaid_report" not in [r["key"] for r in chk.problems(only_taichung)]
    written = []
    assert chk.save_ignored([("fubon", "富邦明細", "桃園")], write=written.append) == 1
    assert written[0][0] == chk.IGNORE_HEADER and written[0][1][:3] == ["fubon", "富邦明細", "桃園"]
    keys = {(k, a) for k, _l, a in chk.ignore_options(rows)}
    assert ("cash_gap", "高雄") in keys and ("prize", "台北") not in keys  # 「—」的不列


def ACTION_HANDLERS_LATE():
    from tools.local_agent import ACTION_HANDLERS

    return ACTION_HANDLERS


def test_newebpay_balance_row_and_parser():
    cash = [["", "台北", "台中", "桃園", "新竹", "高雄"], ["日期", "2026/10/31"],
            ["藍新帳務日期", "2026-11-01 13:01:59", "2026-09-15 10:00:00"], ["藍新帳務金額", "461580", "5"]]
    cells = chk.newebpay_balance_cells(cash, "202610")
    assert cells["台北"][0] == chk.OK and cells["台中"][0] == chk.FAIL and cells["桃園"][0] == chk.FAIL
    assert cells["高雄"][0] == chk.NA
    rows = rows_by_key()
    assert list(rows).index("newebpay_balance") == list(rows).index("newebpay_invoice") + 1
    from tools.newebpay.account_balance import parse_first_balance, query_range

    table = [["處理日期", "處理序號", "處理方式", "商店代號", "前次剩餘金額", "本次金額", "手續費", "本次剩餘金額", "狀態"],
             ["款項來源(結算期間)"],
             ["2026-11-01 13:01:59", "EC2611", "撥款\n(明細)", "MS1", "NT$ 74,872", "NT$ 394,600", "NT$ 7,892",
              "NT$ 461,580", "已撥款"],
             ["信用卡 (2026-10-27~2026-10-27)"],
             ["2026-10-31 13:00:00", "EC2610", "撥款", "MS1", "NT$ 1", "NT$ 2", "NT$ 0", "NT$ 3", "已撥款"]]
    assert parse_first_balance(table) == ("2026-11-01 13:01:59", 461580)
    assert parse_first_balance(table[:2]) is None
    assert query_range("202610", today=date(2026, 11, 5)) == ("2026-10-01", "2026-11-01")
    assert query_range("202612", today=date(2027, 1, 1)) == ("2026-12-01", "2027-01-01")
    from tools.local_agent import ACTION_HANDLERS

    command = ACTION_HANDLERS["newebpay.account_balance"]({"period": "202610", "area": "桃園"})
    assert command[-4:] == ["--part", "balance", "--area", "桃園"]


def test_write_balances_to_cash_gap_sheet():
    from tools.newebpay import account_balance as ab

    calls = []

    class Req:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class Values:
        def get(self, **kw):
            return Req({"values": [["", "台北", "台中", "桃園"], ["日期"]] + [[f"r{i}"] for i in range(3, 15)]
                                  + [["藍新帳務日期"], ["藍新帳務金額"]]})

        def batchUpdate(self, spreadsheetId, body):
            calls.append(body["data"])
            return Req({})

    class Svc:
        def spreadsheets(self):
            return self

        def values(self):
            return Values()

    ab.write_balances({"桃園": ("2026-11-01 09:00:00", 1200)}, service=Svc(), spreadsheet_id="x")
    cells = {d["range"].split("!")[1]: d["values"][0][0] for d in calls[0]}
    assert cells == {"D15": "2026-11-01 09:00:00", "D16": 1200}


def test_newebpay_balance_ok_when_last_record_before_month_end():
    cash = [["", "台北", "台中", "桃園", "新竹", "高雄"], ["日期", "2026/10/31"],
            ["藍新帳務日期", "", "2026-09-30 10:00:00", "", "2026-10-27 13:02:38"], ["藍新帳務金額", "", "5", "", "20192"]]
    cells = chk.newebpay_balance_cells(cash, "202610")
    assert cells["新竹"][0] == chk.OK  # 10/27 之後沒交易，也算查過 10 月
    assert cells["台中"][0] == chk.FAIL  # 9 月的資料＝還沒查 10 月


def test_cash_gap_keeps_previous_value_when_read_fails():
    from tools.finance_management import cash_gap

    class Svc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, **_kw):
            return self

        def execute(self):
            return {"values": [["", "台北", "台中", "桃園", "新竹", "高雄"],
                               ["日期", "2026/10/31", "2026/10/31", "2026/10/31", "2026/10/31", "2026/09/30"],
                               ["富邦餘額", 1, 2, 3, 136198, 9]]}

    results = {"新竹": {"富邦餘額": "錯誤：<HttpError 429>"}, "高雄": {"富邦餘額": "錯誤：<HttpError 429>"}}
    kept = cash_gap.keep_previous_values(Svc(), "x", results, ["新竹", "高雄"], "2026/10/31")
    assert results["新竹"]["富邦餘額"] == 136198 and kept == ["新竹 富邦餘額"]
    assert isinstance(results["高雄"]["富邦餘額"], str)  # 舊值是 9/30 的，不沿用
