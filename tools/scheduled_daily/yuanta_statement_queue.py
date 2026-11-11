"""元大明細下載排程：每月 25 日（時間依主控表「排程設定」，2026-11-12 起 10:00），遇例假日（週六日、國定假日）提前到前一個工作日。

GitHub 只在主控表「本機Agent任務」新增 yuanta.download（台北、台中），由 Mac 上的本機 Agent 執行。
查詢開始日＝財報「元大更新」最後一列的日期（Agent 執行時才讀），結束日＝今天。
排程每月 18～25 日都會叫起來，只有「本月執行日」當天才排任務；排入後在 jenny@hers.com.tw 行事曆建提醒（要開 Mac）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tools.local_agent_queue import create_task

TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]
TARGET_DAY = 25
REMIND_CALENDAR_ID = "jenny@hers.com.tw"


def run_day(year: int, month: int) -> date:
    """25 日；不是工作日就往前找最近的工作日。"""
    from tools.common.tw_holidays import is_workday

    day = date(year, month, TARGET_DAY)
    while not is_workday(day, warn=lambda message: print(message, flush=True)):
        day -= timedelta(days=1)
    return day


def download_params(area: str, now: datetime) -> dict:
    return {
        "start_from": "sheet",
        "end_date": now.strftime("%Y-%m-%d"),
        "area": area,
        "cdp_url": "http://127.0.0.1:9222",
    }


def main() -> None:
    now = datetime.now(TZ)
    target = run_day(now.year, now.month)
    if now.date() != target:
        print(f"本月元大明細下載日為 {target:%Y-%m-%d}；今天 {now:%Y-%m-%d} 不排任務", flush=True)
        return
    for area in AREAS:
        task = create_task("yuanta.download", download_params(area, now), created_by="排程系統")
        print(f"{area}：已建立元大明細下載任務（財報最後一列日期～今天）task_id={task['task_id']}（等待本機 Agent 執行）",
              flush=True)
    remind_agent(now)


def remind_agent(now: datetime) -> None:
    """在 jenny@hers.com.tw 行事曆建會跳通知的提醒：元大明細已排入 Mac，請開機讓 Agent 執行。"""
    try:
        from tools.orders_system import calendar_notify

        calendar_notify.create_notify_event(
            summary="【元大明細】已排入 Mac，請開機讓 Agent 下載",
            description=f"{now:%Y/%m/%d %H:%M} 已排入台北、台中元大明細下載（財報最後一列日期～今天）。",
            start=now, calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[0],
            replace_prefix="【元大明細】")
        print(f"已在 {REMIND_CALENDAR_ID} 建立提醒", flush=True)
    except Exception as exc:
        print(f"⚠️ 行事曆提醒失敗：{exc}", flush=True)


if __name__ == "__main__":
    main()
