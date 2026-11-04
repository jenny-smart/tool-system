"""每月財務排程（雲端，scheduled_finance.yml）。

執行時間不寫在這裡：一律在主控表「排程設定」分頁設定（功能代號＝target），
由 Cloud Schedule Router 依時段派送。預設值見 tools/scheduling/schedule_settings.py。

target：
  fixed_expense           固定費用：台北當月租金／管理費、台中單月當月＋下月租金、台灣連線（記當月）
  fixed_expense_mail      台北查信（AWS／震旦／眾點／台灣連線），期別＝上個月；最後一次（10 日）還缺就建提醒
  fixed_expense_remit     【財報】各區固定費用匯款：第72列有金額、第74列空白的地區 → 排入 Mac 富邦匯款給台北
  vip_full_pipeline       【儲值金】完整流程（上個月）
  revenue_reconcile       四區財報營收 ⑥–⑩
  deposit_report          工具包押金：前月（覆蓋）＋當月統計 → 依統計月份打勾 → 比對異常 → 比對 Y／B1
  review_monthly          年度review：年度預估合計欄公式（AA／AB）＋欄位顯示（期別以前只看預估）
  month_start_queue       排入月初藍新／鯨躍／現金缺口給 Mac
  cash_gap_check          全區現金缺口 → 預收款金額 → 貼 review「現金缺口」區塊 → 列出還沒有數字的項目
  cash_gap_followup       同上（2～10 日重算；上游晚做的數字會補進來），還缺就建提醒
  month_start_check       月初檢查
  accounting_reminder     會計事務所費用確認提醒
  mof_serial              鯨躍字軌（下一期）：盤點各區是否已配號 → 排入財政部登入＋完整流程，提醒登入
  mof_serial_check        字軌未完成提醒
  monthly_checklist       月度盤點：更新主控表「月度盤點」分頁，過期沒做完的合併成一則提醒
  其他：month_start（排入＋檢查）、cash_gap、prepaid_amount

手動：python -m tools.scheduled_finance.runner <target> [--date YYYY-MM-DD]（--date 模擬執行日）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
TARGETS = ("month_start", "month_start_queue", "prepaid_amount", "review_monthly", "fixed_expense", "fixed_expense_mail",
           "fixed_expense_remit", "vip_full_pipeline", "cash_gap", "cash_gap_check", "cash_gap_followup",
           "revenue_reconcile", "deposit_report", "month_start_check", "accounting_reminder", "mof_serial",
           "mof_serial_check", "monthly_checklist")
REMIND_CALENDAR_ID = "jenny@hers.com.tw"


def log(text: str) -> None:
    print(text, flush=True)


def previous_period(today: date) -> str:
    from tools.finance_management.month_start_batch import previous_period as prev

    return prev(today)


def run_month_start_queue(today: date) -> int:
    from tools.finance_management import month_start_batch as batch
    from tools.local_agent_queue import create_task

    period = previous_period(today)
    items = batch.scan(period, log=log)
    steps = [i["step"] for i in items if i["default"]]
    if not steps:
        log(f"{period} 月初四個步驟都已完成或已排入，不重複排")
        return 0
    batch.queue_steps(period, steps, create_task, created_by="排程系統", log=log)
    return 0


def remind(title: str, body: str, prefix: str) -> None:
    """在 Jenny 行事曆建會跳通知的提醒（30 分鐘後開始，提前 20、10 分鐘跳窗）；同一天同開頭的舊提醒會被取代。"""
    from datetime import timedelta

    from tools.orders_system import calendar_notify

    event = calendar_notify.create_notify_event(summary=title, description=body,
                                                start=datetime.now(TW_TZ) + timedelta(minutes=30),
                                                calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10],
                                                replace_prefix=prefix)
    log(f"已在 {(event or {}).get('_calendar_id', REMIND_CALENDAR_ID)} 建立提醒：{title}")


def run_fixed_expense(today: date) -> int:
    """台北當月租金／管理費、台中單月租金（當月＋下月），再查台灣連線（付當月費用，記當月）。"""
    from tools.finance_management.fixed_expense_batch import auto_submit
    from tools.finance_management.taipei_fixed_expense import submit_taipei_fixed_expenses

    auto_submit(today, log=log)
    try:
        result = submit_taipei_fixed_expenses(previous_period(today), run_type="排程", only={"台灣連線"})
        for item in result["items"]:
            log(f"台灣連線：{item['status']}" + (f"（{item['amount']}）" if item["amount"] is not None else ""))
        if result["errors"]:
            log("⚠️ 台灣連線這次沒查到，5／8／10 日查信會再試：" + "；".join(result["errors"]))
    except Exception as exc:
        log(f"⚠️ 台灣連線查信失敗（5／8／10 日會再試）：{exc}")
    return 0


def run_fixed_expense_mail(today: date) -> int:
    """台北查信（AWS／震旦／眾點／台灣連線）；已新增過的會略過。10 日以後還有沒查到的就建提醒。"""
    from tools.finance_management.taipei_fixed_expense import MAIL_ITEMS, submit_taipei_fixed_expenses

    period = previous_period(today)
    result = submit_taipei_fixed_expenses(period, run_type="排程", only=set(MAIL_ITEMS))
    for item in result["items"]:
        log(f"{item['label']}：{item['status']}" + (f"（{item['amount']}）" if item["amount"] is not None else ""))
    if result["errors"] and today.day >= 10:
        remind(f"【固定費用】台北查信還有 {len(result['errors'])} 項沒查到",
               "\n".join(result["errors"]) + "\n\n請確認信件後到 Tools App【財報】台北固定費用請款補做。",
               "【固定費用】台北查信")
    return 1 if result["errors"] and today.day >= 10 else 0


def run_fixed_expense_remit(today: date) -> int:
    """【財報】各區固定費用匯款（排程）：盤點台中／桃園／新竹／高雄 行銷費用總表第 72 列 > 0 且第 74 列空白（尚未匯款）
    的地區 → 排入 Mac 富邦匯款給台北（手機核准；成功後同步富邦明細及原訂分錄），或只建提醒等確認。期別＝當月。"""
    from tools.finance_management.fixed_expense_transfer import preview_fixed_expenses
    from tools.scheduling.schedule_settings import MODE_QUEUE, get_mode

    period = today.strftime("%Y%m")
    preview = preview_fixed_expenses(period, "全區")
    ready, done = [], []
    for plan in preview.get("plans") or []:
        if str(plan.get("transfer_mark") or "").strip():
            done.append(f"{plan['area']}（已匯款 {plan['transfer_mark']}）")
        elif int(plan.get("total") or 0) > 0:
            ready.append(plan)
    lines = [f"{p['area']}：{int(p['total']):,}（{p.get('memo', '')}）" for p in ready]
    log(f"{period} 各區固定費用匯款：待匯款 {len(ready)} 區" + (f"；{'、'.join(done)}" if done else "") +
        (f"；第72列沒有金額：{'、'.join(preview.get('skipped_areas') or [])}" if preview.get("skipped_areas") else ""))
    for line in lines:
        log("  " + line)
    if not ready:
        return 0
    if get_mode("fixed_expense_remit", MODE_QUEUE) == MODE_QUEUE:
        from tools.local_agent_queue import create_task

        for plan in ready:
            task = create_task("fubon.fixed_expense_transfer",
                               {"area": plan["area"], "month": period, "cdp_url": "http://127.0.0.1:9222"},
                               created_by="排程系統")
            log(f"已排入：{plan['area']} 富邦匯款給台北（{task.get('task_id', '')}）")
        remind(f"【固定費用匯款】已排入 {len(ready)} 區富邦匯款，請用手機核准", "\n".join(lines),
               "【固定費用匯款】")
    else:
        remind(f"【固定費用匯款】{len(ready)} 區待匯款，請確認後執行",
               "\n".join(lines) + "\n\n請到 Tools App【財報】各區固定費用匯款勾選「執行富邦銀行匯款給台北」。",
               "【固定費用匯款】")
    return 0


def run_month_start(today: date) -> int:
    """05:00：排入月初整合執行，再做月初檢查（同時段同 workflow 只能派送一次，所以合併）。
    藍新每月 1 日 08:00 才寄手續費發票，所以不在半夜排。"""
    codes = []
    for name, job in (("月初整合執行排入", run_month_start_queue), ("月初檢查", run_month_start_check)):
        log(f"===== {name} =====")
        try:
            codes.append(job(today))
        except Exception as exc:
            log(f"❌ {name}失敗：{exc}")
            codes.append(1)
    return max(codes)


VIP_NO_SOURCE = 2  # 期別資料夾沒有儲值金結算／預收來源檔


def run_vip_full_pipeline(today: date) -> int:
    from config.vip_config import MASTER_SPREADSHEET_ID, ROOT_FOLDER_ID
    from services.google_auth import get_drive_service, get_gspread_client
    from services.google_drive import DriveService
    from services.google_sheets import SheetsService
    from services.vip_workflow import VipStoredValueWorkflow

    period = previous_period(today)
    log(f"【儲值金】完整流程：期別 {period}")
    workflow = VipStoredValueWorkflow(DriveService(get_drive_service()), SheetsService(get_gspread_client()),
                                      MASTER_SPREADSHEET_ID, ROOT_FOLDER_ID)
    progress = lambda msg, *_args, **_kw: log(str(msg))  # noqa: E731
    failed = False
    steps = [
        lambda: workflow.create_monthly_summary(period),
        lambda: workflow.convert_files(period, area="全區", on_progress=progress),
        lambda: workflow.move_files(period, area="全區", on_progress=progress),
        lambda: workflow.apply_formulas(period, area="全區", on_progress=progress),
    ]
    for step in steps:
        result = step()
        log(f"{result.step}：" + ("、".join(result.messages) or "完成"))
        if result.errors:
            log("❌ " + "；".join(result.errors))
            failed = True
        if any("沒有找到需要轉檔" in m for m in result.messages):
            # 00:10 儲值金結算／00:20 儲值金預收沒產出檔案：後面搬運、套公式沒東西可做
            log("❌ 沒有儲值金結算／預收來源檔，請確認 00:10、00:20 月排程有成功，補跑後再手動執行完整流程")
            return VIP_NO_SOURCE
        if failed:
            return 1
    # 彙整完成 → VIP總表 O3:U10 貼到 review「現金缺口」該月欄位
    from tools.finance_management.review_monthly import paste_to_review

    paste_to_review(period, "vip", log=log)
    return 0


def run_cash_gap(today: date) -> int:
    from tools.finance_management.month_start_batch import _run_step

    return _run_step("cash_gap", previous_period(today), "")


# 現金缺口要靠其他功能先做完才有數字的項目（0 或錯誤＝可能還沒做）
CASH_GAP_WATCH = ("富邦餘額", "元大餘額", "行銷費用(廣告)", "總營收", "營業稅", "藍新發票金額")


def cash_gap_missing() -> list[str]:
    """讀主控表「現金缺口試算」，列出還是 0、空白或錯誤的項目。"""
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.finance_management.cash_gap import CASH_GAP_SHEET_NAME
    from tools.finance_management.review_monthly import AREAS

    values = get_sheets_service().spreadsheets().values().get(
        spreadsheetId=get_master_spreadsheet_id(), range=f"'{CASH_GAP_SHEET_NAME}'!A1:G30",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    header = [str(v).strip() for v in (values[0] if values else [])]
    missing = []
    for row in values[2:]:
        label = str(row[0]).strip() if row else ""
        if label not in CASH_GAP_WATCH:
            continue
        empty = []
        for area in AREAS:
            if area not in header:
                continue
            value = row[header.index(area)] if header.index(area) < len(row) else ""
            if isinstance(value, str) or not value:
                empty.append(area)
        if empty:
            missing.append(f"{label}：{'、'.join(empty)}")
    return missing


def run_cash_gap_followup(today: date) -> int:
    """現金缺口 → 預收款金額 → 貼 review「現金缺口」區塊（儲值金＋現金缺口）→ 檢查還沒有數字的項目。
    上游（行銷、營收總表→財報、藍新、富邦／元大明細）晚幾天才做時，2～10 日這裡會重算並補進 review。"""
    from tools.finance_management.review_monthly import paste_to_review

    period = previous_period(today)
    codes = []
    for name, job in (("現金缺口", run_cash_gap), ("預收款金額", run_prepaid_amount)):
        log(f"===== {name} =====")
        try:
            codes.append(job(today))
        except Exception as exc:
            log(f"❌ {name}失敗：{exc}")
            codes.append(1)
    log("===== review 現金缺口區塊 =====")
    paste_to_review(period, "all", log=log)  # 預收款金額寫完再貼一次；儲值金彙整還沒完成時只貼現金缺口
    try:
        missing = cash_gap_missing()
    except Exception as exc:
        missing = [f"讀取現金缺口試算失敗：{exc}"]
    if missing:
        log("⚠️ 現金缺口還沒有數字（0／錯誤）：" + "；".join(missing))
        if today.day <= 10:
            remind(f"【現金缺口】{period[:4]}/{period[4:]} 還有 {len(missing)} 項沒有數字",
                   "\n".join(missing) + "\n\n常見原因：行銷費用、營收總表→財報（總營收→2%／營業稅）、藍新發票、"
                   "富邦／元大明細還沒做。做完後 2～10 日排程會自動重算並更新 review；也可到 Tools App 手動執行"
                   "「All財報現金缺口＋預收款金額」。", "【現金缺口】")
    else:
        log("✅ 現金缺口各項都有數字")
    return max(codes) if codes else 0


def run_month_start_check(today: date) -> int:
    """月初檢查：沒完成的項目在 Jenny 行事曆建會跳通知的提醒（只提醒，不重跑）。"""
    from tools.finance_management.month_start_check import check

    now = datetime.now(TW_TZ)
    if today != now.date():
        now = datetime.combine(today, now.time(), tzinfo=TW_TZ)
    check(now, log=log)
    return 0


def run_revenue_reconcile(today: date) -> int:
    """03:00：四區執行 ⑥–⑩ 金流對帳彙總＋檢核；不搬營收總表、不貼財報。"""
    from tools.finance_management import revenue_batch as batch

    period = previous_period(today) + "-2"
    steps = batch.expand_groups(["cash_flow"])
    failures = batch.run(period, batch.RECONCILE_AREAS, steps, log=log)
    run_month_start_check(today)
    return 1 if failures else 0


def run_prepaid_amount(today: date) -> int:
    """預收款金額（藍新ATM／信用卡／ATM／家電／水洗服務金額），各區寫進「現金缺口試算」下方。"""
    import time

    from tools.prepaid_amount.prepaid_amount import CITIES, run

    failed = 0
    for i, city in enumerate(CITIES):
        if i:
            time.sleep(3)
        try:
            log(run(city, previous_period(today)))
        except Exception as exc:
            failed += 1
            log(f"❌ {city} 預收款金額失敗：{exc}")
    return 1 if failed else 0


def run_review_monthly(today: date) -> int:
    """年度review：年度預估合計欄（AA／AB）公式調整到上個月預估、欄位顯示（期別以前只看預估）。"""
    from tools.finance_management.review_monthly import run_all

    failures = run_all(previous_period(today), log=log)
    if failures:
        remind("【年度review】每月整理有失敗", "\n".join(failures), "【年度review】")
    return 1 if failures else 0


def run_cash_gap_check(today: date) -> int:
    """1 日：全區現金缺口 → 預收款金額 → review 現金缺口區塊 → 檢查還沒有數字的項目。"""
    return run_cash_gap_followup(today)


def run_deposit_report(today: date) -> int:
    """工具包押金財報（台北、台中）：前月覆蓋＋當月統計 → 依統計月份打勾 → 比對異常 → Y／B1。"""
    from tools.finance_management.deposit_report import run_monthly

    problems = []
    for area in ("台北", "台中"):
        try:
            problems.extend(run_monthly(area, today, log=log))
        except Exception as exc:
            problems.append(f"{area} 失敗：{exc}")
            log(f"❌ {area} 工具包押金失敗：{exc}")
    if problems:
        remind(f"【工具包押金】{len(problems)} 項要確認", "\n".join(problems), "【工具包押金】")
    return 1 if any("失敗" in p for p in problems) else 0


MOF_SKIP_AREAS = ("高雄",)  # 跟新竹同一個鯨躍帳號
MOF_ALLOCATE_FUNCTION = "電子發票字軌號碼配號"


def mof_target(today: date) -> tuple[str, str, str]:
    """下一期字軌 → (財政部期別 YYYYMM, 鯨躍查詢年份（民國）, 查詢月份 MM-MM)。"""
    from tools.invoice_center.mof_serial import next_period, period_of

    year, label = next_period(*period_of(today))
    return f"{year}{label[:2]}", str(year - 1911), label


def mof_status(today: date) -> tuple[list[str], list[str], str]:
    """回傳 (已配號地區, 未完成地區, 期別顯示)。依「鯨躍字軌匯入配號Log」成功的配號紀錄判斷。"""
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.invoice_center.invoice_archive import SERIAL_LOG_SHEET, ensure_config_sheet

    _period, qyear, qmonth = mof_target(today)
    sheets = get_sheets_service()
    master = get_master_spreadsheet_id()
    areas = [a for a in ensure_config_sheet(sheets, master) if a not in MOF_SKIP_AREAS]
    rows = sheets.spreadsheets().values().get(spreadsheetId=master, range=f"'{SERIAL_LOG_SHEET}'!A:F"
                                              ).execute().get("values", [])
    period_text = f"{qyear} {qmonth}"
    done = {str(r[2]).strip() for r in rows[1:] if len(r) >= 5 and str(r[1]).strip() == MOF_ALLOCATE_FUNCTION
            and str(r[3]).strip() == period_text and "成功" in str(r[4])}
    return [a for a in areas if a in done], [a for a in areas if a not in done], period_text


def run_mof_serial(today: date) -> int:
    """盤點下一期字軌各區是否已配號 → 沒做的排入「財政部登入＋完整流程」（盤點後自動排入）或只提醒。"""
    from tools.scheduling.schedule_settings import MODE_QUEUE, get_mode

    done, todo, period_text = mof_status(today)
    period, qyear, qmonth = mof_target(today)
    log(f"字軌 {period_text}：已完成 {'、'.join(done) or '無'}；未完成 {'、'.join(todo) or '無'}")
    if not todo:
        return 0
    body = (f"期別 {period_text}（財政部 {period}）未完成：{'、'.join(todo)}\n"
            "財政部登入有圖形驗證碼，請在 Mac 的 Chrome 依序登入各區財政部電子發票平台。")
    if get_mode("mof_serial", MODE_QUEUE) == MODE_QUEUE:
        from tools.local_agent_queue import create_task

        for area in todo:
            params = {"area": area, "cdp_url": "http://127.0.0.1:9222"}
            create_task("mofei.login", params, created_by="排程系統")
            task = create_task("cetustek.full_pipeline", dict(params, month=period, qyear=qyear, qmonth=qmonth),
                               created_by="排程系統")
            log(f"已排入：{area} 財政部登入 → 字軌下載＋匯入＋配號（{task.get('task_id', '')}）")
        remind(f"【鯨躍字軌】已排入 {len(todo)} 區，請登入財政部", body, "【鯨躍字軌】")
    else:
        remind(f"【鯨躍字軌】{len(todo)} 區待處理，請確認後執行",
               body + "\n\n請到 Tools App【整合執行】鯨躍字軌完整流程（財政部下載＋匯入＋配號）執行。", "【鯨躍字軌】")
    return 0


def run_monthly_checklist(today: date) -> int:
    from tools.finance_management.monthly_checklist import scheduled_run

    return scheduled_run(today, log=log)


def run_mof_serial_check(today: date) -> int:
    done, todo, period_text = mof_status(today)
    if not todo:
        log(f"字軌 {period_text} 各區都已配號")
        return 0
    remind(f"【鯨躍字軌】{period_text} 還有 {len(todo)} 區未完成", f"未完成：{'、'.join(todo)}（25 日前要完成）\n"
           "請確認 Mac Agent 任務，或到 Tools App【鯨躍發票】完整流程補做。", "【鯨躍字軌】")
    return 0


def run_accounting_reminder(today: date) -> int:
    from tools.finance_management.accounting_fee_batch import remind

    remind(today, log=log)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="每月財務排程")
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--date", default="", help="YYYY-MM-DD，預設今天（台北時間）")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.date) if args.date else datetime.now(TW_TZ).date()
    log(f"每月財務排程：{args.target}｜執行日 {today}")
    return globals()[f"run_{args.target}"](today)


if __name__ == "__main__":
    sys.exit(main())
