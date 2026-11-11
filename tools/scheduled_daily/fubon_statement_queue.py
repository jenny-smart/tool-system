from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.local_agent_queue import create_task

TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]
# 排程時間依主控表「排程設定」fubon_statement（2026-11-12 起 08:00,11:00,16:30；08:00 要先下載，ATM ①②③ 才能對帳）：
# 第一個時間每天都排（例假日 Mac 有開機就跑，沒開機就等開機），之後的時間只在工作日排。
# 讀不到排程設定時，以這個鐘點前算「每天都排」的那次。
MORNING_CUTOFF_HOUR = 11


def morning_cutoff_hour() -> int:
    """排程設定第二個時間的鐘點（例 08:00,11:00,16:30 → 11）：這個鐘點前的那次每天都排。"""
    try:
        from tools.scheduling.schedule_settings import get_rule

        rule = get_rule("fubon_statement")
        if rule and rule.times:
            return int(rule.times[1][:2]) if len(rule.times) > 1 else 24
    except Exception as exc:
        print(f"⚠️ 讀不到 fubon_statement 的排程時間，用 {MORNING_CUTOFF_HOUR}:00：{exc}", flush=True)
    return MORNING_CUTOFF_HOUR


def should_queue(now: datetime, cutoff_hour: int | None = None) -> bool:
    """第一個時間那次（含延遲到下一個時間前）每天都排；其他時段只有工作日才排。"""
    if now.hour < (morning_cutoff_hour() if cutoff_hour is None else cutoff_hour):
        return True
    from tools.common.tw_holidays import is_workday

    return is_workday(now.date(), warn=lambda message: print(message, flush=True))


def download_params(area: str, now: datetime) -> dict:
    """查詢開始日＝財報「富邦更新」最後一列的日期（Agent 執行時才讀，Mac 晚開機也是最新的）；
    結束日＝今天。已登記的明細會被比對濾掉，不會重複寫入。"""
    return {
        "start_from": "sheet",
        "end_date": now.strftime("%Y-%m-%d"),
        "area": area,
        "resume": "download",
        "cdp_url": "http://127.0.0.1:9222",
    }


def main() -> None:
    now = datetime.now(TZ)
    if not should_queue(now):
        print(f"{now:%Y-%m-%d %H:%M} 為例假日，例假日只排每天第一次的富邦明細下載；本次不排任務", flush=True)
        return
    for area in AREAS:
        task = create_task("fubon.download", download_params(area, now), created_by="排程系統")
        print(f"{area}：已建立富邦明細下載任務（財報最後一列日期～今天）task_id={task['task_id']}（等待本機 Agent 執行）", flush=True)


if __name__ == "__main__":
    main()
