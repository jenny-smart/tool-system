"""【整合執行】每月1日：藍新收退款＋發票金額、鯨躍發票下載、全區現金缺口。

盤點（scan）：列出期別（預設上個月）的三個步驟目前狀態，可做的才預設打勾：
  - 藍新收退款＋手續費發票金額（全區）：一律一起做，每區只登入一次、只輸入一次驗證碼
    （tools.newebpay.month_close：下載期別 1 日～月底收款／退款，發票金額寫入主控表「藍新發票總表」）
  - 鯨躍發票（全區，期別整月；每區只登入一次）→ 本機 Agent cetustek.download，同一次登入裡：
    下載全月／紙本／單月中獎發票並歸檔、台中有買方統編的紙本發票 PDF 建檔；
    下載完再更新紙本發票、單月中獎發票到年度發票檔（只讀 Drive，不用再登入）
  - 藍新、鯨躍的高雄跟新竹同一個帳號：只登入新竹，下載完複製一份、地區改成高雄（檔案、發票金額都有高雄的）
  - 全區現金缺口（截至期別月底，寫入「現金缺口試算」） → 只讀寫 Google 試算表
  狀態依「本機Agent任務」裡同期別最新一筆任務判斷（排隊中／執行中不能再排，已完成預設不勾）；
  現金缺口另看「藍新發票總表」有沒有該期別的發票金額、「現金缺口試算」是不是已算到期別月底。
執行：勾選的步驟整批排成一筆本機 Agent 任務（month_start.batch_run），依序：
  藍新收退款＋發票金額 → 鯨躍發票 → 現金缺口。藍新登入每區仍需人工輸入驗證碼（一次）。
  每一步失敗都不影響後面的步驟（現金缺口照算，藍新發票金額沒更新就用藍新發票總表目前的數字）。
  各步驟結果寫進任務的 result_json（steps），盤點時逐步顯示，不會因為整批失敗就全部顯示失敗。
  全部跑完在 Jenny 行事曆留一筆紀錄（不跳提醒）。
排程：每月 1 日 05:00 由雲端排入全部三個步驟（藍新約 08:00 寄手續費發票，還沒開就算失敗、可再補做；Mac 開機、輸入驗證碼時才會執行）；
  02:00 雲端另外先算一次現金缺口（不等藍新），藍新做完後這裡會再算一次覆蓋。
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
RECORD_CALENDAR_ID = "jenny@hers.com.tw"
BATCH_ACTION = "month_start.batch_run"
CDP_URL = "http://127.0.0.1:9222"

# 步驟 → (顯示名稱, 本機 Agent action；空字串＝不經瀏覽器，直接讀寫試算表)
STEPS = {
    "newebpay": ("藍新收退款＋發票金額＋帳務", ""),  # tools.newebpay.month_close，每區只登入一次
    "cetustek_download": ("鯨躍發票下載＋紙本／中獎更新＋台中統編PDF", "cetustek.download"),
    "cash_gap": ("全區現金缺口", ""),
}
STEP_ORDER = tuple(STEPS)
# 舊版分成兩步（已排入的舊任務照樣認得，執行時一律合併成一步）
LEGACY_NEWEBPAY_STEPS = ("newebpay_download", "newebpay_invoice")
# 現金缺口一律照算（2026-11 Jenny：要能自己跑）；藍新發票金額沒更新時會用藍新發票總表目前的數字
DEPENDS_ON: dict[str, str] = {}
STEP_RESULTS_PREFIX = "STEP_RESULTS:"  # 整批任務結束時印出各步驟結果，本機 Agent 存進 result_json


def normalize_steps(steps) -> list[str]:
    steps = ["newebpay" if s in LEGACY_NEWEBPAY_STEPS else s for s in steps]
    return [s for s in STEP_ORDER if s in steps]

# 高雄跟新竹用同一個藍新／鯨躍帳號，跑新竹一次就好；台中有買方統編的紙本發票要建 PDF
SHARED_ACCOUNT_SKIP = ("高雄",)
# 高雄不登入，改由新竹下載完複製一份、地區改成高雄歸檔
SHARED_ACCOUNT_COPIES = ("新竹=高雄",)
PAPER_PDF_AREAS = ("台中",)

ACTIVE = {"pending": "已排入，等待本機 Agent", "running": "執行中", "cancel_requested": "正在中止"}


def previous_period(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y%m")


def period_range(period: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{6}", period or "") or not 1 <= int(period[4:]) <= 12:
        raise ValueError("期別請輸入 YYYYMM，例如 202610")
    year, month = int(period[:4]), int(period[4:])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def step_params(step: str, period: str) -> dict[str, Any]:
    period_range(period)
    if step == "newebpay":
        return {"period": period, "cdp_url": CDP_URL}
    if step == "cetustek_download":
        return {"month": period, "start_date": "", "end_date": "", "area": "全區", "format": "csv", "cdp_url": CDP_URL,
                "skip_areas": list(SHARED_ACCOUNT_SKIP), "copy_areas": list(SHARED_ACCOUNT_COPIES),
                "paper_pdf_areas": list(PAPER_PDF_AREAS)}
    if step == "cash_gap":
        return {"period": period}
    raise ValueError(f"不支援的步驟：{step}")


def _params(task: dict) -> dict:
    try:
        data = json.loads(task.get("params_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def task_covers(task: dict, step: str, period: str) -> bool:
    """這筆本機 Agent 任務是不是做「這個期別的這個步驟」（整合執行或單獨排的任務都算）。"""
    params = _params(task)
    action = task.get("action", "")
    if action == BATCH_ACTION:
        return params.get("period") == period and step in normalize_steps(params.get("steps") or [])
    if action != STEPS[step][1] or not action:
        return False
    if step == "cetustek_download":
        return str(params.get("month") or "") == period and params.get("area") in ("", "全區", None)
    return False


def latest_task(tasks: list[dict], step: str, period: str) -> Optional[dict]:
    """tasks 是 list_tasks() 的結果（新的在前）。"""
    return next((t for t in tasks if task_covers(t, step, period)), None)


def step_result(task: dict, step: str) -> Optional[str]:
    """整批任務 result_json 裡這一步的結果（成功／失敗…／略過…）；舊任務沒有就回傳 None。"""
    try:
        steps = json.loads(task.get("result_json") or "{}").get("steps") or {}
    except (TypeError, ValueError, AttributeError):
        return None
    if step == "newebpay" and step not in steps:
        legacy = [steps.get(s) for s in LEGACY_NEWEBPAY_STEPS if s in steps]
        if legacy:
            return "成功" if all(v == "成功" for v in legacy) else "失敗"
    return steps.get(step)


def _status_text(task: Optional[dict], step: str = "") -> tuple[str, bool, bool]:
    """回傳 (狀態文字, 可勾選, 預設打勾)。"""
    if task is None:
        return "尚未執行", True, True
    status = task.get("status", "")
    when = task.get("finished_at") or task.get("started_at") or task.get("created_at") or ""
    if status in ACTIVE:
        return f"{ACTIVE[status]}（{task.get('created_at', '')}）", False, False
    result = step_result(task, step) if step and task.get("action") == BATCH_ACTION else None
    if result is not None and status in ("completed", "failed"):
        if result == "成功":
            return f"已完成（{when}）", True, False
        return f"上次{result}（{when}）", True, True
    if status == "completed":
        return f"已完成（{when}）", True, False
    if status == "cancelled":
        return f"已取消（{when}）", True, True
    return f"上次失敗（{when}）", True, True


def invoice_amount_areas(values: list[list], period: str) -> list[str]:
    """「藍新發票總表」裡已有該期別發票金額的地區（A 地區、B 月份、D 金額有值）。
    藍新每月 1 日 08:00 才開手續費發票，太早查會寫入「查無發票」（D 空白），不算有資料。"""
    areas = []
    for row in values[1:]:
        area = str(row[0] if row else "").strip()
        month = str(row[1] if len(row) > 1 else "").strip()
        amount = str(row[3] if len(row) > 3 else "").strip()
        if month == period and area and amount and area not in areas:
            areas.append(area)
    return areas


def cash_gap_date(values: list[list]) -> str:
    """「現金缺口試算」第 2 列（日期）B 欄；沒算過回傳空字串。"""
    return str(values[1][1]).strip() if len(values) > 1 and len(values[1]) > 1 else ""


def build_items(period: str, tasks: list[dict], invoice_values: list[list], cash_gap_values: list[list],
                balance_done: bool = True) -> list[dict]:
    """純邏輯（方便測試）：依任務紀錄與試算表內容盤點三個步驟。"""
    _start, end = period_range(period)
    items = []
    for step in STEP_ORDER:
        label = STEPS[step][0]
        task = latest_task(tasks, step, period)
        status, selectable, default = _status_text(task, step)
        note = ""
        if step == "newebpay":
            areas = invoice_amount_areas(invoice_values, period)
            note = f"藍新發票總表已有：{'、'.join(areas)}" if areas else \
                "藍新發票總表還沒有這個期別的發票金額（藍新每月 1 日 08:00 才寄發票）"
            if not balance_done:
                note += "；藍新帳務還沒查這個期別"
            if areas and task is None and balance_done:
                status, default = "已有資料（之前手動或排程做過）", False
            elif areas and task is None:
                status, default = "收退款／發票已有資料，但藍新帳務還沒查", True
            elif not areas and selectable and status.startswith("已完成"):
                status, default = f"{status}，但還沒有發票金額", True
        if step == "cash_gap":
            computed = cash_gap_date(cash_gap_values)
            done = computed == end.strftime("%Y/%m/%d")
            has_invoice = bool(invoice_amount_areas(invoice_values, period))
            note = (f"現金缺口試算已算到 {computed}" if computed else "現金缺口試算還沒有資料") + \
                   ("" if has_invoice else "；藍新發票金額還沒寫入，建議跟藍新一起勾")
            if task is None:
                status = f"已試算（截至 {computed}）" if done else "尚未試算這個期別"
                default = not done or not has_invoice
        items.append({"step": step, "label": label, "status": status, "selectable": selectable,
                      "default": default and selectable, "note": note,
                      "task_id": (task or {}).get("task_id", "")})
    return items


def scan(period: str, log: Callable[[str], None] = print) -> list[dict]:
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.finance_management.cash_gap import CASH_GAP_SHEET_NAME, NEWEBPAY_INVOICE_SHEET
    from tools.local_agent_queue import list_tasks

    period_range(period)
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()

    def read(title: str, rng: str) -> list[list]:
        try:
            return service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{title}'!{rng}") \
                .execute().get("values", [])
        except Exception as exc:
            log(f"⚠️ 讀取「{title}」失敗：{exc}")
            return []

    try:
        tasks = list_tasks(limit=1000, service=service, spreadsheet_id=spreadsheet_id)
    except Exception as exc:
        log(f"⚠️ 讀取本機Agent任務失敗：{exc}")
        tasks = []
    cash = read(CASH_GAP_SHEET_NAME, "A1:G40")
    return build_items(period, tasks, read(NEWEBPAY_INVOICE_SHEET, "A:D"), cash, balance_done=_balance_done(cash, period))


def _balance_done(cash: list[list], period: str) -> bool:
    """現金缺口試算的藍新帳務日期／金額：該查的地區都已查到這個期別。"""
    from tools.finance_management.month_start_check import OK, newebpay_balance_cells
    from tools.newebpay.account_balance import NO_BALANCE_AREAS

    cells = newebpay_balance_cells(cash, period)
    return all(icon == OK for area, (icon, _t) in cells.items() if area not in NO_BALANCE_AREAS)


def queue_steps(period: str, steps: list[str], create_task: Callable, created_by: str = "月初整合執行",
                log: Callable[[str], None] = print) -> Optional[dict]:
    steps = normalize_steps(steps)
    if not steps:
        return None
    period_range(period)
    task = create_task(BATCH_ACTION, {"period": period, "steps": steps, "cdp_url": CDP_URL}, created_by=created_by)
    log(f"已排入：{period} {'→'.join(STEPS[s][0] for s in steps)} → {task.get('task_id', '')}")
    return task


def _step_command(step: str, period: str, cdp_url: str) -> list[str]:
    from tools.local_agent import ACTION_HANDLERS

    params = dict(step_params(step, period), cdp_url=cdp_url)
    return ACTION_HANDLERS[STEPS[step][1]](params)


def _run_step(step: str, period: str, cdp_url: str) -> int:
    if step == "newebpay":
        # 收退款＋發票金額一起做：每區只登入一次（只輸入一次驗證碼）
        from tools.newebpay.month_close import INVOICE_FAILED

        sys.stdout.flush()
        code = subprocess.run([sys.executable, "-m", "tools.newebpay.month_close", period,
                               "--cdp-url", cdp_url or CDP_URL]).returncode
        if not code & INVOICE_FAILED and not _invoice_ready(period):
            print("❌ 藍新發票總表還沒有這個期別的發票金額（藍新每月 1 日 08:00 才寄發票），請晚點再執行", flush=True)
            code |= INVOICE_FAILED
        return code
    if step == "cash_gap":
        from tools.finance_management.cash_gap import write_cash_gap_sheet

        _start, end = period_range(period)
        results = write_cash_gap_sheet(end, on_progress=lambda msg, _level="info": print(msg, flush=True))
        errors = [f"{area} {key}" for area, r in results.items() for key, v in r.items() if isinstance(v, str)]
        if errors:
            print("⚠️ 現金缺口有項目出錯：" + "、".join(errors), flush=True)
        # 現金缺口算完（含藍新發票金額）→ 貼到 review「現金缺口」該月欄位
        from tools.finance_management.review_monthly import paste_to_review

        paste_to_review(period, "cash", log=lambda t: print(t, flush=True))
        return 0
    sys.stdout.flush()
    code = subprocess.run(_step_command(step, period, cdp_url)).returncode
    if step == "cetustek_download":
        code = max(code, update_invoice_sheets(period))
    return code


def update_invoice_sheets(period: str, log: Callable[[str], None] = lambda t: print(t, flush=True)) -> int:
    """鯨躍下載完：把 Drive 上的紙本發票、單月中獎發票更新到各區年度發票檔（不用登入鯨躍）。"""
    from tools.invoice_center.invoice_archive import InvoiceArchiveProcessor, previous_prize_period

    processor = InvoiceArchiveProcessor()
    prize = previous_prize_period(period)
    failures = []
    for area in list(processor.configs):  # 高雄的檔案是新竹複製過去的，一樣要更新
        jobs = [("紙本發票更新", lambda a=area: processor.update_paper(area=a, yyyymm=period), period)]
        if prize:
            jobs.append(("中獎發票更新", lambda a=area: processor.update_prize_period(area=a, period8=prize), prize))
        for name, job, job_period in jobs:
            try:
                count = job()
                processor.write_update_log(function_name=name, area=area, period=job_period, status="成功", count=count)
                log(f"[{area}] {name}完成：{count} 筆")
            except Exception as exc:
                failures.append(f"{area} {name}")
                processor.write_update_log(function_name=name, area=area, period=job_period, status="失敗",
                                           message=str(exc))
                log(f"❌ [{area}] {name}失敗：{exc}")
    if not prize:
        log(f"{period} 是雙月，沒有單月中獎發票要更新")
    return 1 if failures else 0


def _invoice_ready(period: str) -> bool:
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.finance_management.cash_gap import NEWEBPAY_INVOICE_SHEET

    values = get_sheets_service().spreadsheets().values().get(
        spreadsheetId=get_master_spreadsheet_id(), range=f"'{NEWEBPAY_INVOICE_SHEET}'!A:D").execute().get("values", [])
    return bool(invoice_amount_areas(values, period))


def run_plan(period: str, steps: list[str], log: Callable[[str], None] = print, cdp_url: str = CDP_URL,
             run_step: Callable[[str, str, str], int] = _run_step, record: bool = True) -> int:
    results: dict[str, str] = {}
    steps = normalize_steps(steps)
    for step in steps:
        label = STEPS[step][0]
        dependency = DEPENDS_ON.get(step)
        if dependency in steps and results.get(dependency) != "成功":
            results[step] = f"略過（{STEPS[dependency][0]}沒有成功）"
            log(f"⏭️ {label}：{results[step]}")
            continue
        log(f"===== {period} {label} =====")
        try:
            code = run_step(step, period, cdp_url)
        except Exception as exc:
            log(f"❌ {label}無法執行：{exc}")
            code = 1
        results[step] = "成功" if code == 0 else _failure_text(step, code)
        log(("✅ " if code == 0 else "❌ ") + f"{label}：{results[step]}")
    log(STEP_RESULTS_PREFIX + json.dumps(results, ensure_ascii=False))
    if record:
        try:
            _record(period, results, log)
        except Exception as exc:
            log(f"⚠️ 行事曆紀錄建立失敗：{exc}")
    return 0 if all(v == "成功" for v in results.values()) else 1


def _failure_text(step: str, code: int) -> str:
    if step == "newebpay":
        from tools.newebpay.month_close import BALANCE_FAILED, DOWNLOAD_FAILED, INVOICE_FAILED

        parts = [name for bit, name in ((DOWNLOAD_FAILED, "收退款"), (INVOICE_FAILED, "發票金額"),
                                        (BALANCE_FAILED, "帳務")) if code & bit]
        if parts:
            return f"失敗（{'、'.join(parts)}）"
    return f"失敗（exit {code}）"


def build_record_text(period: str, results: dict[str, str], now: datetime) -> tuple[str, str]:
    ok = sum(1 for v in results.values() if v == "成功")
    title = f"月初整合執行 {period}：成功 {ok}／{len(results)} 項"
    lines = [f"{now:%Y/%m/%d %H:%M}"] + [f"{STEPS[s][0]}：{v}" for s, v in results.items()]
    return title, "\n".join(lines)


def _record(period: str, results: dict[str, str], log: Callable[[str], None]) -> None:
    if not results:
        return
    from tools.orders_system import calendar_notify

    now = datetime.now(TW_TZ)
    title, body = build_record_text(period, results, now)
    event = calendar_notify.create_notify_event(summary=title, description=body, start=now,
                                                calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[],
                                                replace_prefix=title.split("：")[0])
    log(f"已在 {(event or {}).get('_calendar_id', RECORD_CALENDAR_ID)} 行事曆留紀錄：{title}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="月初整合執行：藍新收退款＋發票金額、鯨躍發票下載、現金缺口")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--period", required=True)
    parser.add_argument("--steps", default=",".join(STEP_ORDER))
    parser.add_argument("--cdp-url", default=CDP_URL)
    args = parser.parse_args(argv)
    log = lambda text: print(text, flush=True)  # noqa: E731
    return run_plan(args.period, [s for s in args.steps.split(",") if s], log=log, cdp_url=args.cdp_url)


if __name__ == "__main__":
    sys.exit(main())
