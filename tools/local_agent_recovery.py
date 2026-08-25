from __future__ import annotations

import json

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import default_agent_id, list_tasks, now_text, update_task


def recover_interrupted_tasks(agent_id: str = "") -> int:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    agent_id = agent_id or default_agent_id()
    recovered = 0

    for task in list_tasks(limit=500, service=service, spreadsheet_id=spreadsheet_id):
        if task.get("status") != "running":
            continue
        if str(task.get("agent_id") or "") != agent_id:
            continue

        message = "Agent 上次執行中斷，已自動結束"
        update_task(
            int(task["_row"]),
            {
                "status": "failed",
                "finished_at": now_text(),
                "message": message,
                "result_json": json.dumps(
                    {"error": message, "recovered": True},
                    ensure_ascii=False,
                ),
            },
            service=service,
            spreadsheet_id=spreadsheet_id,
        )
        recovered += 1

    if recovered:
        print(f"Recovered interrupted Agent tasks: {recovered}", flush=True)
    return recovered


def main() -> int:
    recover_interrupted_tasks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
