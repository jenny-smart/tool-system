"""月初檢查：每月 1 日 02:00 起每小時（02:00～23:00）檢查 1 日該完成的工作，沒完成就在 Jenny 行事曆建會跳通知的提醒。

檢查項目：
  - 雲端月排程：預收、儲值金結算、儲值金預收（scheduled_monthly）、儲值金完整流程（scheduled_finance）
    → 看 repo 裡當天 logs/YYYYMMDD/*.json 的執行結果（沒有 json＝沒跑）
  - 固定費用：當月台北租金／管理費、單月台中租金是不是已新增到請款記錄
  - 月初藍新收退款、藍新發票金額、鯨躍發票、現金缺口：同【整合執行】月初藍新／鯨躍／現金缺口的盤點
    （排隊等 Mac、失敗、沒做都算未完成）
02:00 那次跟現金缺口同一個排程，先算完現金缺口再檢查（同一時段同一個 workflow 只能派送一次）。
全部完成就不建提醒。只提醒、不自動重跑：要人工驗證碼的步驟，等有空時再到整合執行按「盤點→執行」。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"
LOG_ROOT = Path(__file__).resolve().parents[2] / "logs"

# (顯示名稱, logs 裡的 json 名稱, 補跑方式)
CLOUD_JOBS = [
    ("預收", "monthly_prepaid_report", "GitHub Actions → Scheduled Monthly Jobs → target prepaid_report"),
    ("儲值金結算", "monthly_stored_value_settlement",
     "GitHub Actions → Scheduled Monthly Jobs → target stored_value_settlement"),
    ("儲值金預收", "monthly_stored_value_prepaid",
     "GitHub Actions → Scheduled Monthly Jobs → target stored_value_prepaid"),
    ("儲值金完整流程", "finance_vip_full_pipeline",
     "Tools App【儲值金】完整流程，或 GitHub Actions → Scheduled Finance Jobs → target vip_full_pipeline"),
]
MONTH_START_FIX = "Tools App【整合執行】月初藍新／鯨躍／現金缺口 → 盤點 → 執行"
FIXED_EXPENSE_FIX = "Tools App【整合執行】固定費用（辦公室租金）→ 盤點 → 新增"


def read_job_log(day: date, name: str, root: Path = LOG_ROOT) -> Optional[dict]:
    path = root / day.strftime("%Y%m%d") / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cloud_problems(day: date, read=read_job_log) -> list[dict]:
    problems = []
    for label, name, fix in CLOUD_JOBS:
        result = read(day, name)
        if result is None:
            problems.append({"label": label, "status": "沒有執行紀錄", "fix": fix})
        elif result.get("status") != "success":
            problems.append({"label": label, "status": f"失敗（exit {result.get('exit_code', '?')}）", "fix": fix})
    return problems


def month_start_problems(items: list[dict]) -> list[dict]:
    """月初整合執行的盤點結果：預設打勾（沒做／失敗）或不能勾（排隊等 Mac、執行中）都是未完成。"""
    return [{"label": i["label"], "status": i["status"], "fix": MONTH_START_FIX}
            for i in items if i["default"] or not i["selectable"]]


def fixed_expense_problems(items: list[dict]) -> list[dict]:
    return [{"label": f"固定費用 {i['area']} {i['memo']}", "status": i["status"], "fix": FIXED_EXPENSE_FIX}
            for i in items if i["default"] or i["status"] == "請款記錄讀取失敗"]


def build_reminder(day: date, now: datetime, problems: list[dict]) -> tuple[str, str]:
    lines = [f"{now:%Y/%m/%d %H:%M} 檢查，{len(problems)} 項還沒完成：", ""]
    for p in problems:
        lines.append(f"・{p['label']}：{p['status']}")
    lines += ["", "處理方式："]
    for fix in dict.fromkeys(p["fix"] for p in problems):
        lines.append(f"・{fix}")
    lines += ["", "1 日每小時會再檢查一次（到 23:00），全部完成就不再提醒。"]
    return f"月初工作未完成 {len(problems)} 項（{day:%m/%d}）", "\n".join(lines)


def collect(day: date, log: Callable[[str], None] = print) -> list[dict]:
    from tools.finance_management import fixed_expense_batch, month_start_batch

    problems = cloud_problems(day)
    try:
        problems += fixed_expense_problems(fixed_expense_batch.scan(day.strftime("%Y%m"), log=log))
    except Exception as exc:
        problems.append({"label": "固定費用", "status": f"檢查失敗：{exc}", "fix": FIXED_EXPENSE_FIX})
    try:
        items = month_start_batch.scan(month_start_batch.previous_period(day), log=log)
        problems += month_start_problems(items)
    except Exception as exc:
        problems.append({"label": "月初藍新／鯨躍／現金缺口", "status": f"檢查失敗：{exc}", "fix": MONTH_START_FIX})
    return problems


def check(now: Optional[datetime] = None, log: Callable[[str], None] = print) -> list[dict]:
    now = now or datetime.now(TW_TZ)
    problems = collect(now.date(), log=log)
    if not problems:
        log("月初工作全部完成，不建提醒 🎉")
        return []
    title, body = build_reminder(now.date(), now, problems)
    log(title + "\n" + body)
    from tools.orders_system import calendar_notify

    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去（20、10 分鐘前各跳一次）
    calendar_notify.create_notify_event(summary=title, description=body, start=now + timedelta(minutes=30),
                                        calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10])
    log(f"已在 {REMIND_CALENDAR_ID} 行事曆建提醒")
    return problems
