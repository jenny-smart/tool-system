"""【整合執行】每月1日：藍新收退款＋發票金額、鯨躍發票下載、全區現金缺口。

盤點（scan）：列出期別（預設上個月）的四個步驟目前狀態，可做的才預設打勾：
  - 藍新收退款下載（全區，期別 1 日～月底）        → 本機 Agent newebpay.download
  - 藍新手續費發票金額（全區，寫入主控表「藍新發票總表」）→ 本機 Agent newebpay.invoice_amounts
  - 鯨躍發票下載（全區，期別整月）                 → 本機 Agent cetustek.download
  - 全區現金缺口（截至期別月底，寫入「現金缺口試算」） → 只讀寫 Google 試算表
  狀態依「本機Agent任務」裡同期別最新一筆任務判斷（排隊中／執行中不能再排，已完成預設不勾）；
  現金缺口另看「藍新發票總表」有沒有該期別的發票金額、「現金缺口試算」是不是已算到期別月底。
執行：勾選的步驟整批排成一筆本機 Agent 任務（month_start.batch_run），依序：
  藍新收退款 → 藍新發票金額 → 鯨躍發票 → 現金缺口。藍新登入每區仍需人工輸入驗證碼。
  藍新發票金額失敗就不算現金缺口（會用到舊數字）；其他步驟失敗不影響後面的步驟。
  全部跑完在 Jenny 行事曆留一筆紀錄（不跳提醒）。
排程：每月 1 日 09:00 由雲端排入全部四個步驟（藍新 08:00 才寄手續費發票；Mac 開機、輸入驗證碼時才會執行）；
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
RECORD_CALENDAR_ID = "jenny@lemonclean.com.tw"
BATCH_ACTION = "month_start.batch_run"
CDP_URL = "http://127.0.0.1:9222"

# 步驟 → (顯示名稱, 本機 Agent action；空字串＝不經瀏覽器，直接讀寫試算表)
STEPS = {
    "newebpay_download": ("藍新收退款下載", "newebpay.download"),
    "newebpay_invoice": ("藍新手續費發票金額", "newebpay.invoice_amounts"),
    "cetustek_download": ("鯨躍發票下載", "cetustek.download"),
    "cash_gap": ("全區現金缺口", ""),
}
STEP_ORDER = tuple(STEPS)
# 依賴：現金缺口要用藍新發票金額，發票金額這步失敗就不算現金缺口
DEPENDS_ON = {"cash_gap": "newebpay_invoice"}

ACTIVE = {"pending": "已排入，等待本機 Agent", "running": "執行中", "cancel_requested": "正在中止"}


def previous_period(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y%m")


def period_range(period: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{6}", period or "") or not 1 <= int(period[4:]) <= 12:
        raise ValueError("期別請輸入 YYYYMM，例如 202610")
    year, month = int(period[:4]), int(period[4:])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def step_params(step: str, period: str) -> dict[str, Any]:
    start, end = period_range(period)
    if step == "newebpay_download":
        return {"start_date": start.isoformat(), "end_date": end.isoformat(), "area": "全區", "cdp_url": CDP_URL}
    if step == "newebpay_invoice":
        return {"months": period, "area": "全區", "cdp_url": CDP_URL}
    if step == "cetustek_download":
        return {"month": period, "start_date": "", "end_date": "", "area": "全區", "format": "csv", "cdp_url": CDP_URL}
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
        return params.get("period") == period and step in (params.get("steps") or [])
    if action != STEPS[step][1] or not action:
        return False
    if step == "newebpay_download":
        start, end = period_range(period)
        return params.get("start_date") == start.isoformat() and params.get("end_date") == end.isoformat() \
            and params.get("area") in ("", "全區", None)
    if step == "newebpay_invoice":
        return str(params.get("months") or "") == period and params.get("area") in ("", "全區", None)
    if step == "cetustek_download":
        return str(params.get("month") or "") == period and params.get("area") in ("", "全區", None)
    return False


def latest_task(tasks: list[dict], step: str, period: str) -> Optional[dict]:
    """tasks 是 list_tasks() 的結果（新的在前）。"""
    return next((t for t in tasks if task_covers(t, step, period)), None)


def _status_text(task: Optional[dict]) -> tuple[str, bool, bool]:
    """回傳 (狀態文字, 可勾選, 預設打勾)。"""
    if task is None:
        return "尚未執行", True, True
    status = task.get("status", "")
    when = task.get("finished_at") or task.get("started_at") or task.get("created_at") or ""
    if status in ACTIVE:
        return f"{ACTIVE[status]}（{task.get('created_at', '')}）", False, False
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


def build_items(period: str, tasks: list[dict], invoice_values: list[list], cash_gap_values: list[list]) -> list[dict]:
    """純邏輯（方便測試）：依任務紀錄與試算表內容盤點四個步驟。"""
    _start, end = period_range(period)
    items = []
    for step in STEP_ORDER:
        label = STEPS[step][0]
        task = latest_task(tasks, step, period)
        status, selectable, default = _status_text(task)
        note = ""
        if step == "newebpay_invoice":
            areas = invoice_amount_areas(invoice_values, period)
            note = f"藍新發票總表已有：{'、'.join(areas)}" if areas else \
                "藍新發票總表還沒有這個期別的發票金額（藍新每月 1 日 08:00 才寄發票）"
            if areas and task is None:
                status, default = "已有資料（之前手動或排程做過）", False
            elif not areas and selectable and status.startswith("已完成"):
                status, default = f"{status}，但還沒有發票金額", True
        if step == "cash_gap":
            computed = cash_gap_date(cash_gap_values)
            done = computed == end.strftime("%Y/%m/%d")
            has_invoice = bool(invoice_amount_areas(invoice_values, period))
            note = (f"現金缺口試算已算到 {computed}" if computed else "現金缺口試算還沒有資料") + \
                   ("" if has_invoice else "；藍新發票金額還沒寫入，建議跟藍新發票金額一起勾")
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
    return build_items(period, tasks, read(NEWEBPAY_INVOICE_SHEET, "A:D"), read(CASH_GAP_SHEET_NAME, "A1:C2"))


def queue_steps(period: str, steps: list[str], create_task: Callable, created_by: str = "月初整合執行",
                log: Callable[[str], None] = print) -> Optional[dict]:
    steps = [s for s in STEP_ORDER if s in steps]
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
    if step == "cash_gap":
        from tools.finance_management.cash_gap import write_cash_gap_sheet

        _start, end = period_range(period)
        results = write_cash_gap_sheet(end, on_progress=lambda msg, _level="info": print(msg, flush=True))
        errors = [f"{area} {key}" for area, r in results.items() for key, v in r.items() if isinstance(v, str)]
        if errors:
            print("⚠️ 現金缺口有項目出錯：" + "、".join(errors), flush=True)
        return 0
    sys.stdout.flush()
    code = subprocess.run(_step_command(step, period, cdp_url)).returncode
    if step == "newebpay_invoice" and code == 0 and not _invoice_ready(period):
        # 查得到頁面但還沒開發票（例如 1 日 08:00 前）：算失敗，現金缺口就不會用 0 去算
        print("❌ 藍新發票總表還沒有這個期別的發票金額（藍新每月 1 日 08:00 才寄發票），請晚點再執行", flush=True)
        return 3
    return code


def _invoice_ready(period: str) -> bool:
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.finance_management.cash_gap import NEWEBPAY_INVOICE_SHEET

    values = get_sheets_service().spreadsheets().values().get(
        spreadsheetId=get_master_spreadsheet_id(), range=f"'{NEWEBPAY_INVOICE_SHEET}'!A:D").execute().get("values", [])
    return bool(invoice_amount_areas(values, period))


def run_plan(period: str, steps: list[str], log: Callable[[str], None] = print, cdp_url: str = CDP_URL,
             run_step: Callable[[str, str, str], int] = _run_step, record: bool = True) -> int:
    results: dict[str, str] = {}
    for step in [s for s in STEP_ORDER if s in steps]:
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
        results[step] = "成功" if code == 0 else f"失敗（exit {code}）"
        log(("✅ " if code == 0 else "❌ ") + f"{label}：{results[step]}")
    if record:
        try:
            _record(period, results, log)
        except Exception as exc:
            log(f"⚠️ 行事曆紀錄建立失敗：{exc}")
    return 0 if all(v == "成功" for v in results.values()) else 1


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
    calendar_notify.create_notify_event(summary=title, description=body, start=now,
                                        calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[])
    log(f"已在 {RECORD_CALENDAR_ID} 行事曆留紀錄：{title}")


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
