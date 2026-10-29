"""同一台 Agent 前一筆任務還在跑時不接新任務；清潔異動整合執行只排一筆逐區任務。"""
import json
from datetime import datetime

from tools import local_agent, local_agent_queue as queue

NOW = datetime(2026, 10, 29, 16, 0, 0)


def task(status, agent_id="mac", started_at="2026-10-29 15:55:29"):
    return {"status": status, "agent_id": agent_id, "started_at": started_at}


def test_busy_task_blocks_only_recent_running_task_of_same_agent():
    assert queue.busy_task([task("running")], "mac", NOW)
    assert queue.busy_task([task("cancel_requested")], "mac", NOW)
    assert queue.busy_task([task("running", agent_id="other")], "mac", NOW) is None
    assert queue.busy_task([task("completed")], "mac", NOW) is None
    assert queue.busy_task([task("running", started_at="2026-10-29 13:00:00")], "mac", NOW) is None


def test_claim_next_task_waits_while_agent_busy(monkeypatch):
    tasks = [task("pending", agent_id=""), task("running")]
    monkeypatch.setattr(queue, "ensure_task_sheet", lambda service, spreadsheet_id: (None, "sid"))
    monkeypatch.setattr(queue, "list_tasks", lambda **_: tasks)
    monkeypatch.setattr(queue, "busy_task", lambda items, agent_id: items[1])
    monkeypatch.setattr(queue, "update_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應接任務")))
    assert queue.claim_next_task(agent_id="mac") is None


def test_batch_run_command():
    plan = [{"region": "台北", "steps": {"allowance": [7]}, "sync_rows": [7]}]
    command = local_agent.ACTION_HANDLERS["change_order.batch_run"]({"plan": plan})
    assert command[1:4] == ["-m", "tools.finance_management.change_order_batch", "run"]
    assert json.loads(command[command.index("--plan") + 1]) == plan
