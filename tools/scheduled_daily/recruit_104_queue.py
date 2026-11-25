"""排程：排入 Mac 本機 Agent 任務「104 週報」（recruit104.weekly_stats）。

2026-11-26 Jenny：每週五 04:40，遇例假日提前（主控表「排程設定」recruit_104_weekly）。
GitHub 不登入 104；Agent 在已登入 104 招募管理的 Agent Chrome 裡抓每週數據，
寫台北名冊 104統計副表 A:L 與 104統計表（從統計表最後一天補到 Agent 執行日的昨天）。
Mac 沒開機時任務排隊，開機後執行。
"""
from __future__ import annotations

from tools.local_agent_queue import create_task


def main() -> None:
    task = create_task("recruit104.weekly_stats", {"cdp_url": "http://127.0.0.1:9222"}, created_by="排程系統")
    print(f"已建立 104 週報任務 task_id={task['task_id']}（等待本機 Agent 執行；補到執行日的昨天）", flush=True)


if __name__ == "__main__":
    main()
