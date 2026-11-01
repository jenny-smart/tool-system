"""每月財務排程（雲端，scheduled_finance.yml）。時間一律台北時間：

  1 日 00:30  fixed_expense        【整合執行】固定費用：新增當月台北辦公室租金／管理費、單月台中當月＋下月租金
  1 日 01:00  vip_full_pipeline    【儲值金】完整流程（複製＋轉檔＋搬運＋套用公式），上個月
                                   （00:10 儲值金結算、00:20 儲值金預收的檔案要先產出）
  1 日 02:00  cash_gap_check       全區現金缺口（截至上個月底，藍新發票金額這時還沒有）＋月初檢查
  1 日 09:00  month_start          排入【整合執行】月初藍新／鯨躍／現金缺口（藍新 08:00 才寄手續費發票）＋月初檢查
  1 日 03:00～23:00 每小時（09:00 除外）  month_start_check  月初檢查：沒完成的項目在 Jenny 行事曆建提醒
                                   （09:00 前不提醒藍新／鯨躍／現金缺口，還沒到排入時間）
  單月 10 日 10:00  accounting_reminder  會計事務所費用確認：在 Jenny 行事曆建提醒
  其他 target：month_start_queue（只排入）、cash_gap（只算現金缺口）

手動：python -m tools.scheduled_finance.runner <target> [--date YYYY-MM-DD]（--date 模擬執行日）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
TARGETS = ("month_start", "month_start_queue", "fixed_expense", "vip_full_pipeline", "cash_gap", "cash_gap_check",
           "month_start_check", "accounting_reminder")


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


def run_fixed_expense(today: date) -> int:
    from tools.finance_management.fixed_expense_batch import auto_submit

    auto_submit(today, log=log)
    return 0


def run_month_start(today: date) -> int:
    """09:00：排入月初整合執行，再做月初檢查（同時段同 workflow 只能派送一次，所以合併）。
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
            return 1
        if failed:
            return 1
    return 0


def run_cash_gap(today: date) -> int:
    from tools.finance_management.month_start_batch import _run_step

    return _run_step("cash_gap", previous_period(today), "")


def run_month_start_check(today: date) -> int:
    """月初檢查：沒完成的項目在 Jenny 行事曆建會跳通知的提醒（只提醒，不重跑）。"""
    from tools.finance_management.month_start_check import check

    now = datetime.now(TW_TZ)
    if today != now.date():
        now = datetime.combine(today, now.time(), tzinfo=TW_TZ)
    check(now, log=log)
    return 0


def run_cash_gap_check(today: date) -> int:
    """02:00：先算現金缺口，再做月初檢查（同一時段同一個 workflow 只能派送一次，所以合併）。"""
    code = 1
    try:
        code = run_cash_gap(today)
    except Exception as exc:
        log(f"❌ 現金缺口失敗：{exc}")
    run_month_start_check(today)
    return code


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
