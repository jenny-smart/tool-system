from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.local_agent_queue import create_task

TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]
# 排程：每天 08:00；11:00、16:30 只在工作日（例假日只跑 08:00）
MORNING_CUTOFF_HOUR = 10


def should_queue(now: datetime) -> bool:
    """08:00 那次（含延遲到 10 點前）每天都排；其他時段只有工作日才排。"""
    if now.hour < MORNING_CUTOFF_HOUR:
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
        print(f"{now:%Y-%m-%d %H:%M} 為例假日，例假日只在 08:00 下載富邦明細；本次不排任務", flush=True)
        return
    for area in AREAS:
        task = create_task("fubon.download", download_params(area, now), created_by="排程系統")
        print(f"{area}：已建立富邦明細下載任務（財報最後一列日期～今天）task_id={task['task_id']}（等待本機 Agent 執行）", flush=True)


if __name__ == "__main__":
    main()
