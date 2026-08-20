from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.local_agent_queue import create_task

TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]


def main() -> None:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    for area in AREAS:
        task = create_task(
            "fubon.download",
            {
                "start_date": today,
                "end_date": today,
                "area": area,
                "resume": "download",
                "cdp_url": "http://127.0.0.1:9222",
            },
            created_by="排程系統",
        )
        print(f"{area}：已建立富邦明細下載任務 task_id={task['task_id']}（等待本機 Agent 執行）", flush=True)


if __name__ == "__main__":
    main()
